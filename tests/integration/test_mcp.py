"""MCP server tests, through a real MCP client (in-process, plus one stdio subprocess test)."""

import json
import sys
from pathlib import Path

import anyio
import pytest
import yaml

mcp = pytest.importorskip("mcp")

from mcp import Client, StdioServerParameters  # noqa: E402

from sqlsentry import SQLSentry  # noqa: E402
from sqlsentry.config import (  # noqa: E402
    DataSourceConfig,
    LLMSettings,
    ProviderConfig,
    Settings,
    StoreSettings,
)
from sqlsentry.llm import FakeProvider  # noqa: E402
from sqlsentry.mcp_server import build_server  # noqa: E402
from sqlsentry.store import MemoryStore  # noqa: E402

STORE_DDL = Path(__file__).resolve().parents[2] / "examples" / "store_schema.sql"
BASE_TOOLS = {"list_datasources", "describe_schema", "validate_sql", "run_sql"}


@pytest.fixture
def fake():
    return FakeProvider(name="fake")


def make_sentry(sample_db_url, store_policy, fake=None):
    datasources = {
        "store": DataSourceConfig(
            url=sample_db_url,
            description="Fictional store",
            policy=store_policy,
            glossary=["revenue = SUM(order_items.quantity * order_items.unit_price)"],
        ),
        "ddl": DataSourceConfig(schema_file=str(STORE_DDL), dialect="postgres", policy=store_policy),
    }
    llm = (
        LLMSettings(default="fake", providers={"fake": ProviderConfig(type="fake")})
        if fake
        else LLMSettings()
    )
    return SQLSentry(
        Settings(datasources=datasources, llm=llm, store=StoreSettings(url=None)),
        providers={"fake": fake} if fake else None,
        store=MemoryStore(),
    )


def call(server, tool, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})

    return anyio.run(go)


def tools_of(server):
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools, c.instructions

    return anyio.run(go)


@pytest.fixture
def server(sample_db_url, store_policy):
    return build_server(make_sentry(sample_db_url, store_policy))


# ---------------------------------------------------------------- discovery


def test_without_llm_only_gateway_tools_are_offered(server):
    tools, instructions = tools_of(server)
    assert {t.name for t in tools} == BASE_TOOLS
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)
    assert "read-only" in instructions and "describe_schema" in instructions


def test_with_llm_generate_and_ask_are_offered(sample_db_url, store_policy, fake):
    tools, _ = tools_of(build_server(make_sentry(sample_db_url, store_policy, fake)))
    assert {t.name for t in tools} == BASE_TOOLS | {"generate_sql", "ask"}


def test_list_datasources(server):
    r = call(server, "list_datasources")
    by_name = {d["name"]: d for d in r.structured_content["result"]}
    assert by_name["store"]["can_execute"] is True and by_name["store"]["mode"] == "connected"
    assert by_name["ddl"]["can_execute"] is False and by_name["ddl"]["dialect"] == "postgres"


def test_describe_schema_is_policy_filtered(server):
    s = call(server, "describe_schema", {"datasource": "store"}).structured_content
    assert "TABLE customers" in s["schema_text"]
    assert "email" not in s["schema_text"] and "internal_audit_log" not in s["schema_text"]
    assert s["glossary"] and s["can_execute"] is True


# ---------------------------------------------------------------- the guard, as an agent sees it


def test_run_sql_returns_rows_and_the_sql_as_run(server):
    r = call(server, "run_sql", {"datasource": "store", "sql": "SELECT name FROM products ORDER BY name"})
    out = r.structured_content
    assert not r.is_error and out["columns"] == ["name"] and out["row_count"] == 20
    assert "LIMIT" in out["sql"]  # the guard's rewrite is shown to the agent
    assert any("LIMIT" in w for w in out["warnings"])


@pytest.mark.parametrize(
    "sql,code",
    [
        ("DELETE FROM orders", "unsafe_sql"),
        ("SELECT 1; DROP TABLE orders", "unsafe_sql"),
        ("SELECT email FROM customers", "invalid_sql"),
        ("SELECT * FROM internal_audit_log", "invalid_sql"),
        ("SELECT pg_sleep(10)", "unsafe_sql"),
    ],
)
def test_run_sql_rejections_are_readable_errors(server, sql, code):
    r = call(server, "run_sql", {"datasource": "store", "sql": sql})
    assert r.is_error
    assert code in r.content[0].text and "rejected" in r.content[0].text


def test_select_star_never_returns_hidden_columns(server):
    out = call(
        server, "run_sql", {"datasource": "store", "sql": "SELECT * FROM customers"}
    ).structured_content
    assert "email" not in out["columns"] and "country" in out["columns"]


def test_row_caps(server):
    out = call(server, "run_sql", {"datasource": "store", "sql": "SELECT id FROM orders", "max_rows": 5})
    assert out.structured_content["row_count"] == 5 and out.structured_content["truncated"] is True
    big = call(server, "run_sql", {"datasource": "store", "sql": "SELECT id FROM orders", "max_rows": 10**6})
    assert big.structured_content["row_count"] == 100  # the policy's max_rows wins


def test_validate_sql(server):
    ok = call(server, "validate_sql", {"datasource": "store", "sql": "SELECT name FROM products"})
    assert ok.structured_content["valid"] and "LIMIT" in ok.structured_content["sql"]
    bad = call(server, "validate_sql", {"datasource": "store", "sql": "UPDATE orders SET status = 'x'"})
    assert not bad.is_error and bad.structured_content["valid"] is False
    assert "unsafe_sql" in bad.structured_content["errors"][0]


def test_schema_only_datasource_validates_but_never_runs(server):
    ok = call(server, "validate_sql", {"datasource": "ddl", "sql": "SELECT name FROM products"})
    assert ok.structured_content["valid"]
    r = call(server, "run_sql", {"datasource": "ddl", "sql": "SELECT name FROM products"})
    assert r.is_error and "execution_unavailable" in r.content[0].text


def test_unknown_datasource(server):
    r = call(server, "describe_schema", {"datasource": "nope"})
    assert r.is_error and "datasource_not_found" in r.content[0].text


# ---------------------------------------------------------------- delegating to sqlsentry's model


def test_ask_and_generate(sample_db_url, store_policy, fake):
    server = build_server(make_sentry(sample_db_url, store_policy, fake))
    fake.push({"sql": "SELECT COUNT(*) AS n FROM orders WHERE status = 'pending'", "explanation": "Counts."})
    a = call(server, "ask", {"datasource": "store", "question": "pending orders?"}).structured_content
    assert a["status"] == "ok" and a["result"]["rows"] == [[155]] and a["explanation"] == "Counts."

    fake.push({"sql": "", "needs_clarification": True, "clarification_question": "Which year?"})
    g = call(server, "generate_sql", {"datasource": "store", "question": "revenue"}).structured_content
    assert g["status"] == "needs_clarification" and g["clarification_question"] == "Which year?"


# ---------------------------------------------------------------- real stdio subprocess


def test_stdio_server_end_to_end(sample_db_path, tmp_path):
    """Launch `sqlsentry-mcp` the way an MCP client does and talk to it over stdin/stdout."""
    cfg = tmp_path / "sqlsentry.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "datasources": {
                    "store": {
                        "url": f"sqlite:///{Path(sample_db_path).as_posix()}",
                        "policy": {"tables": {"include": ["*"]}, "columns": {"hidden": ["customers.email"]},
                                   "allow_execute": True},
                    }
                },
                "store": {"url": None},
            }
        )
    )  # fmt: skip
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "sqlsentry.mcp_server", "--config", str(cfg)], cwd=str(tmp_path)
    )

    async def go():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            r = await c.call_tool(
                "run_sql", {"datasource": "store", "sql": "SELECT COUNT(*) AS n FROM orders"}
            )
            bad = await c.call_tool("run_sql", {"datasource": "store", "sql": "DROP TABLE orders"})
            return names, r, bad

    names, r, bad = anyio.run(go)
    assert names == BASE_TOOLS
    assert json.loads(r.content[0].text)["rows"] == [[900]]
    assert bad.is_error and "unsafe_sql" in bad.content[0].text

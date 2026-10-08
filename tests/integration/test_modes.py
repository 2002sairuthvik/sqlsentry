"""The two ways to use sqlsentry: SQL only (incl. schema-only datasources) and batteries
included (generate + run in one call), plus running SQL the user edited."""

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sqlsentry import SQLSentry
from sqlsentry.cli import main
from sqlsentry.config import (
    DataSourceConfig,
    LLMSettings,
    ProviderConfig,
    ServerSettings,
    Settings,
    StoreSettings,
)
from sqlsentry.errors import ExecutionUnavailable, InvalidSQLError, PermissionDenied
from sqlsentry.llm import FakeProvider
from sqlsentry.policy import Consumer, Grant
from sqlsentry.server.app import create_app
from sqlsentry.server.auth import hash_key
from sqlsentry.store import MemoryStore

STORE_DDL = Path(__file__).resolve().parents[2] / "examples" / "store_schema.sql"
KEY = "sqs_modes_test_key"
GEN_ONLY_KEY = "sqs_modes_gen_only"
H = {"Authorization": f"Bearer {KEY}"}


def ok(sql, **kw):
    return {"sql": sql, "explanation": "e", **kw}


@pytest.fixture
def fake():
    return FakeProvider(name="fake")


@pytest.fixture
def settings(sample_db_url, store_policy):
    return Settings(
        datasources={
            "store": DataSourceConfig(url=sample_db_url, policy=store_policy),
            "ddl": DataSourceConfig(schema_file=str(STORE_DDL), dialect="postgres", policy=store_policy),
        },
        llm=LLMSettings(default="fake", providers={"fake": ProviderConfig(type="fake")}),
        consumers=[
            Consumer(
                name="team",
                key_sha256=[hash_key(KEY)],
                grants=[Grant(datasource="*", scopes=["generate", "execute", "validate", "schema"])],
            ),
            Consumer(
                name="gen-only",
                key_sha256=[hash_key(GEN_ONLY_KEY)],
                grants=[Grant(datasource="*", scopes=["generate"])],
            ),
        ],
        server=ServerSettings(rate_limit_per_minute=0),
        store=StoreSettings(url=None),
    )


@pytest.fixture
def sentry(settings, fake):
    return SQLSentry(settings, providers={"fake": fake}, store=MemoryStore())


@pytest.fixture
def client(sentry):
    return TestClient(create_app(sentry))


# ---------------------------------------------------------------- library


def test_schema_only_generate_and_validate(sentry, fake):
    fake.push(ok("-- pending orders\nSELECT COUNT(*) AS n FROM orders WHERE status = 'pending'"))
    gen = sentry.generate("ddl", "how many pending orders")
    assert gen.status == "ok" and gen.dialect == "postgres"
    assert any("schema-only" in w for w in gen.warnings)
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "One of: pending, shipped, delivered, cancelled" in prompt  # DDL comments reach the model
    assert "email" not in prompt and "internal_audit_log" not in prompt
    assert not sentry.validate("ddl", "SELECT email FROM customers").valid


def test_schema_only_cannot_execute(sentry, fake):
    fake.push(ok("SELECT name FROM products"))
    gen = sentry.generate("ddl", "products")
    with pytest.raises(ExecutionUnavailable):
        sentry.execute(gen)
    with pytest.raises(ExecutionUnavailable):
        sentry.execute_sql("ddl", "SELECT name FROM products")


def test_ask_on_schema_only_fails_before_calling_the_model(sentry, fake):
    with pytest.raises(ExecutionUnavailable):
        sentry.ask("ddl", "products")
    assert fake.calls == []


def test_ask_returns_sql_and_rows(sentry, fake):
    fake.push(ok("SELECT COUNT(*) AS n FROM orders WHERE status = 'pending'"))
    a = sentry.ask("store", "how many pending orders")
    assert a.generation.status == "ok" and a.result.rows == [[155]]


def test_ask_clarification_has_no_result(sentry, fake):
    fake.push(ok("", needs_clarification=True, clarification_question="Which period?"))
    a = sentry.ask("store", "revenue")
    assert a.generation.status == "needs_clarification" and a.result is None


def test_ask_checks_allow_execute_before_calling_model(sentry, fake):
    sentry.settings.datasources["store"].policy.allow_execute = False
    with pytest.raises(PermissionDenied):
        sentry.ask("store", "anything")
    assert fake.calls == []


def test_edited_sql_runs_but_is_still_guarded(sentry):
    res = sentry.execute_sql("store", "SELECT COUNT(*) FROM orders WHERE status = 'cancelled'")
    assert res.rows == [[156]] and res.generation_id is None
    with pytest.raises(InvalidSQLError):
        sentry.execute_sql("store", "SELECT email FROM customers")


# ---------------------------------------------------------------- REST API


def test_query_returns_sql_and_rows(client, fake):
    fake.push(ok("SELECT COUNT(*) AS n FROM orders WHERE status = 'pending'"))
    r = client.post("/v1/sql/query", json={"datasource": "store", "question": "pending orders"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and "pending" in body["sql"] and body["result"]["rows"] == [[155]]


def test_query_needs_execute_scope(client, fake):
    headers = {"Authorization": f"Bearer {GEN_ONLY_KEY}"}
    r = client.post("/v1/sql/query", json={"datasource": "store", "question": "q"}, headers=headers)
    assert r.status_code == 403
    assert fake.calls == []


def test_query_on_schema_only_is_409_without_model_call(client, fake):
    r = client.post("/v1/sql/query", json={"datasource": "ddl", "question": "q"}, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "execution_unavailable"
    assert fake.calls == []


def test_execute_edited_sql_over_api(client):
    body = {"datasource": "store", "sql": "SELECT COUNT(*) AS n FROM orders WHERE status = 'shipped'"}
    r = client.post("/v1/sql/execute", json=body, headers=H)
    assert r.status_code == 200 and r.json()["generation_id"] is None
    r = client.post("/v1/sql/execute", json={"datasource": "store", "sql": "DELETE FROM orders"}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsafe_sql"
    r = client.post(
        "/v1/sql/execute",
        json={"datasource": "store", "sql": "SELECT 1"},
        headers={"Authorization": f"Bearer {GEN_ONLY_KEY}"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"generation_id": "gen_x", "datasource": "store", "sql": "SELECT 1"},
        {"datasource": "store"},
        {"sql": "SELECT 1"},
    ],
)
def test_execute_body_must_be_exactly_one_form(client, body):
    r = client.post("/v1/sql/execute", json=body, headers=H)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_datasource_list_reports_modes(client):
    info = {d["id"]: d for d in client.get("/v1/datasources", headers=H).json()}
    assert info["store"]["mode"] == "connected" and info["store"]["can_execute"] is True
    assert info["ddl"]["mode"] == "schema_only" and info["ddl"]["can_execute"] is False
    assert info["ddl"]["dialect"] == "postgres"


# ---------------------------------------------------------------- CLI export-context


def test_export_context_then_use_as_schema_only(sample_db_path, tmp_path, capsys):
    src = tmp_path / "src.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "llm": {"default": "fake", "providers": {"fake": {"type": "fake"}}},
                "datasources": {
                    "store": {
                        "url": f"sqlite:///{Path(sample_db_path).as_posix()}",
                        "policy": {
                            "tables": {"include": ["*"], "exclude": ["internal_*"]},
                            "columns": {"hidden": ["customers.email"]},
                        },
                    }
                },
                "store": {"url": None},
            }
        )
    )
    bundle = tmp_path / "store.schema.json"
    assert main(["export-context", "store", "-c", str(src), "-o", str(bundle)]) == 0
    data = json.loads(bundle.read_text())
    assert "internal_audit_log" not in [t["name"] for t in data["tables"]]
    assert "email" not in [c["name"] for t in data["tables"] for c in t["columns"]]

    only = tmp_path / "only.yaml"
    only.write_text(
        yaml.safe_dump(
            {
                "llm": {"default": "fake", "providers": {"fake": {"type": "fake"}}},
                "datasources": {
                    "copy": {
                        "schema_file": bundle.name,
                        "dialect": "sqlite",
                        "policy": {"tables": {"include": ["*"]}},
                    }
                },
                "store": {"url": None},
            }
        )
    )
    capsys.readouterr()
    assert main(["inspect", "copy", "-c", str(only)]) == 0
    out = capsys.readouterr().out
    assert "schema-only" in out and "TABLE customers" in out
    assert main(["ask", "copy", "q", "-c", str(only), "--execute"]) == 1
    assert "execution_unavailable" in capsys.readouterr().err

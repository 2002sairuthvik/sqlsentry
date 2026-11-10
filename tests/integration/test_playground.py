"""The browser playground: what visitors can and can't reach."""

import pytest
from fastapi.testclient import TestClient

from sqlsentry import SQLSentry
from sqlsentry.config import (
    DataSourceConfig,
    LLMSettings,
    PlaygroundSettings,
    ProviderConfig,
    ServerSettings,
    Settings,
    StoreSettings,
)
from sqlsentry.llm import FakeProvider
from sqlsentry.policy import Consumer, Grant
from sqlsentry.server.app import create_app
from sqlsentry.server.auth import hash_key
from sqlsentry.server.playground import CustomSchemas
from sqlsentry.store import MemoryStore

DDL = """CREATE TABLE teams (id integer PRIMARY KEY, name text);
CREATE TABLE players (id integer PRIMARY KEY, team_id integer REFERENCES teams(id), goals integer);"""
ANSWER = {
    "sql": "SELECT COUNT(*) AS n FROM orders WHERE status = 'pending'",
    "explanation": "Counts pending.",
}


@pytest.fixture
def fake():
    return FakeProvider(name="fake")


@pytest.fixture
def make(sample_db_url, store_policy, fake):
    def _make(**pg):
        playground = PlaygroundSettings(
            **{"enabled": True, "datasources": ["store"], "requests_per_minute": 0, **pg}
        )
        settings = Settings(
            datasources={
                "store": DataSourceConfig(url=sample_db_url, policy=store_policy, description="Sample store"),
                "secret": DataSourceConfig(
                    url=sample_db_url, policy=store_policy, description="Not for visitors"
                ),
            },
            llm=LLMSettings(default="fake", providers={"fake": ProviderConfig(type="fake", model="fake-1")}),
            consumers=[Consumer(name="team", key_sha256=[hash_key("sqs_k")], grants=[Grant(datasource="*")])],
            server=ServerSettings(playground=playground, rate_limit_per_minute=0),
            store=StoreSettings(url=None),
        )
        return TestClient(create_app(SQLSentry(settings, providers={"fake": fake}, store=MemoryStore())))

    return _make


def test_disabled_by_default(sample_db_url, store_policy):
    settings = Settings(datasources={"store": DataSourceConfig(url=sample_db_url, policy=store_policy)},
                        store=StoreSettings(url=None))  # fmt: skip
    c = TestClient(create_app(SQLSentry(settings, store=MemoryStore())))
    assert c.get("/playground").status_code == 404
    assert c.get("/", follow_redirects=False).headers["location"] == "/docs"


def test_page_and_root_redirect(make):
    c = make()
    assert c.get("/", follow_redirects=False).headers["location"] == "/playground"
    page = c.get("/playground")
    assert page.status_code == 200 and "Try to break the guard" in page.text
    assert "innerHTML" not in page.text  # model output and visitor input are only ever set as text


def test_info_shows_only_playground_datasources(make):
    info = (
        make(sample_questions={"store": ["How many orders are pending?"]}).get("/playground/api/info").json()
    )
    assert [d["name"] for d in info["datasources"]] == ["store"]
    ds = info["datasources"][0]
    assert (
        ds["can_execute"] and "email" not in ds["schema_text"] and ds["hidden_columns"] == ["customers.email"]
    )
    assert ds["sample_questions"] == ["How many orders are pending?"] and info["model"] == "fake-1"


def test_ask_sample_database_runs_the_query(make, fake):
    fake.push(ANSWER)
    a = make().post("/playground/api/ask", json={"datasource": "store", "question": "pending?"}).json()
    assert a["status"] == "ok" and a["result"]["rows"] == [[155]] and a["explanation"] == "Counts pending."


def test_ask_without_execute(make, fake):
    fake.push(ANSWER)
    a = (
        make()
        .post("/playground/api/ask", json={"datasource": "store", "question": "q", "execute": False})
        .json()
    )
    assert a["result"] is None and a["sql"]


def test_unlisted_datasource_is_unreachable(make, fake):
    c = make()
    for path, body in [("ask", {"question": "q"}), ("validate", {"sql": "SELECT 1"})]:
        r = c.post(f"/playground/api/{path}", json={"datasource": "secret", **body})
        assert r.status_code == 404
    assert fake.calls == []


def test_pasted_schema_generates_but_never_runs(make, fake):
    c = make()
    goals_sql = (
        "SELECT t.name, SUM(p.goals) AS goals FROM teams t JOIN players p ON p.team_id = t.id GROUP BY t.name"
    )
    fake.push({"sql": goals_sql, "explanation": "Goals per team."})
    a = c.post(
        "/playground/api/ask", json={"schema_ddl": DDL, "dialect": "postgres", "question": "goals per team"}
    ).json()
    assert (
        a["status"] == "ok" and a["result"] is None and "not run" in a["note"] and a["dialect"] == "postgres"
    )
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "TABLE players" in prompt and "TABLE orders" not in prompt
    # The temporary datasource is invisible to API consumers, even with a '*' grant.
    names = [d["id"] for d in c.get("/v1/datasources", headers={"Authorization": "Bearer sqs_k"}).json()]
    assert names == ["store", "secret"]


def test_schema_preview_and_guard_on_pasted_schema(make):
    c = make()
    r = c.post("/playground/api/schema", json={"schema_ddl": DDL, "dialect": "postgres"}).json()
    assert r["tables"] == ["teams", "players"] and "FOREIGN KEY (team_id)" in r["schema_text"]
    v = c.post(
        "/playground/api/validate",
        json={"schema_ddl": DDL, "dialect": "postgres", "sql": "DELETE FROM teams"},
    )
    assert v.json()["valid"] is False and v.json()["errors"][0]["code"] == "unsafe_sql"


@pytest.mark.parametrize(
    "ddl,pg,match",
    [
        ("   ", {}, "empty"),
        ("CREATE INDEX i ON t (a);", {}, "No CREATE TABLE"),
        ("x" * 1200, {"custom_schema_max_chars": 1000}, "longer than 1000"),
        (DDL, {"allow_custom_schema": False}, "disabled"),
    ],
)
def test_pasted_schema_limits(make, ddl, pg, match):
    r = make(**pg).post("/playground/api/schema", json={"schema_ddl": ddl, "dialect": "postgres"})
    assert r.status_code == 400 and match in r.json()["error"]["message"]


def test_bad_dialect_rejected(make):
    r = make().post("/playground/api/schema", json={"schema_ddl": DDL, "dialect": "excel"})
    assert r.status_code == 400


def test_guard_sandbox(make):
    c = make()
    bad = c.post(
        "/playground/api/validate", json={"datasource": "store", "sql": "SELECT email FROM customers"}
    ).json()
    assert bad["valid"] is False
    ok = c.post(
        "/playground/api/validate", json={"datasource": "store", "sql": "SELECT * FROM customers"}
    ).json()
    assert ok["valid"] and "email" not in ok["sql"].lower()


def test_rate_limit_is_per_visitor_and_before_the_model(make, fake):
    c = make(requests_per_minute=2, trust_proxy_headers=True)
    fake.push(ANSWER, ANSWER, ANSWER)
    ask = {"datasource": "store", "question": "q", "execute": False}
    a = {"X-Forwarded-For": "1.1.1.1"}
    codes = [c.post("/playground/api/ask", json=ask, headers=a).status_code for _ in range(3)]
    assert codes == [200, 200, 429] and len(fake.calls) == 2
    assert c.post("/playground/api/ask", json=ask, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_proxy_header_ignored_unless_trusted(make, fake):
    c = make(requests_per_minute=1)
    fake.push(ANSWER, ANSWER)
    ask = {"datasource": "store", "question": "q", "execute": False}
    assert c.post("/playground/api/ask", json=ask, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    # A spoofed header can't buy a fresh bucket when proxy headers aren't trusted.
    assert c.post("/playground/api/ask", json=ask, headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 429


def test_custom_schemas_are_bounded(sample_db_url, store_policy):
    sentry = SQLSentry(
        Settings(datasources={"store": DataSourceConfig(url=sample_db_url, policy=store_policy)},
                 store=StoreSettings(url=None)),
        store=MemoryStore(),
    )  # fmt: skip
    schemas = CustomSchemas(sentry, limit=2)
    names = [schemas.register(f"CREATE TABLE t{i} (id int);", "postgres", 10) for i in range(3)]
    live = set(sentry.settings.datasources)
    assert names[0] not in live and names[1] in live and names[2] in live
    assert (
        schemas.register("CREATE TABLE t2 (id int);", "postgres", 10) == names[2]
    )  # same DDL reuses its slot


def test_head_requests_for_health_checks(make):
    c = make()
    assert c.head("/", follow_redirects=False).status_code == 307
    assert c.head("/health").status_code == 200

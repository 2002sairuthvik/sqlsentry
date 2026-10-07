import pytest
from fastapi.testclient import TestClient

from sqlsentry import SQLSentry
from sqlsentry.client import SQLSentryAPIError, SQLSentryClient
from sqlsentry.config import (
    DataSourceConfig,
    LLMSettings,
    ProviderConfig,
    ServerSettings,
    Settings,
    StoreSettings,
)
from sqlsentry.llm import FakeProvider
from sqlsentry.policy import Consumer, Grant
from sqlsentry.server.app import create_app
from sqlsentry.server.auth import hash_key
from sqlsentry.store import MemoryStore

KEYS = {"full": "sqs_full_key", "gen_only": "sqs_gen_only", "other_ds": "sqs_other_ds"}


def ok(sql):
    return {"sql": sql, "explanation": "e"}


@pytest.fixture
def fake():
    return FakeProvider(name="fake")


@pytest.fixture
def make_client(sample_db_url, store_policy, fake):
    def _make(rate_limit=0, auth=True):
        settings = Settings(
            datasources={
                "store": DataSourceConfig(url=sample_db_url, policy=store_policy, description="Store"),
                "hr": DataSourceConfig(url=sample_db_url, policy=store_policy, description="Secret HR"),
            },
            llm=LLMSettings(default="fake", providers={"fake": ProviderConfig(type="fake")}),
            consumers=[
                Consumer(
                    name="team-full",
                    key_sha256=[hash_key(KEYS["full"])],
                    grants=[Grant(datasource="store", scopes=["generate", "execute", "validate", "schema"])],
                ),
                Consumer(
                    name="team-gen",
                    key_sha256=[hash_key(KEYS["gen_only"])],
                    grants=[Grant(datasource="store", scopes=["generate"])],
                ),
                Consumer(
                    name="team-hr",
                    key_sha256=[hash_key(KEYS["other_ds"])],
                    grants=[Grant(datasource="hr", scopes=["generate", "execute"])],
                ),
            ],
            server=ServerSettings(auth_enabled=auth, rate_limit_per_minute=rate_limit),
            store=StoreSettings(url=None),
        )
        sentry = SQLSentry(settings, providers={"fake": fake}, store=MemoryStore())
        return TestClient(create_app(sentry))

    return _make


def h(name):
    return {"Authorization": f"Bearer {KEYS[name]}"}


def test_health_needs_no_auth(make_client):
    r = make_client().get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["X-Request-ID"].startswith("req_")


def test_missing_and_bad_keys(make_client):
    c = make_client()
    r = c.post("/v1/sql/generate", json={"datasource": "store", "question": "q"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    r = c.post(
        "/v1/sql/generate", json={"datasource": "store", "question": "q"}, headers={"X-API-Key": "nope"}
    )
    assert r.status_code == 401


def test_generate_execute_flow(make_client, fake):
    c = make_client()
    fake.push(ok("-- cheapest products\nSELECT name, unit_price FROM products ORDER BY unit_price LIMIT 3"))
    r = c.post(
        "/v1/sql/generate", json={"datasource": "store", "question": "cheapest products"}, headers=h("full")
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and "cheapest products" in body["sql"] and body["explanation"] == "e"

    r = c.post("/v1/sql/execute", json={"generation_id": body["generation_id"]}, headers=h("full"))
    assert r.status_code == 200 and r.json()["row_count"] == 3

    r = c.get(f"/v1/generations/{body['generation_id']}", headers=h("full"))
    assert r.status_code == 200 and r.json()["sql"] == body["sql"]

    r = c.post(
        f"/v1/generations/{body['generation_id']}/feedback", json={"rating": "correct"}, headers=h("full")
    )
    assert r.status_code == 201


def test_scope_enforcement(make_client, fake):
    c = make_client()
    fake.push(ok("SELECT name FROM products"))
    gen = c.post(
        "/v1/sql/generate", json={"datasource": "store", "question": "q"}, headers=h("gen_only")
    ).json()
    r = c.post("/v1/sql/execute", json={"generation_id": gen["generation_id"]}, headers=h("gen_only"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = c.post("/v1/sql/validate", json={"datasource": "store", "sql": "SELECT 1"}, headers=h("gen_only"))
    assert r.status_code == 403


def test_datasource_isolation(make_client, fake):
    c = make_client()
    r = c.post("/v1/sql/generate", json={"datasource": "hr", "question": "salaries"}, headers=h("full"))
    assert r.status_code == 404  # not granted looks exactly like not existing
    assert r.json()["error"]["code"] == "datasource_not_found"
    assert fake.calls == []
    assert [d["id"] for d in c.get("/v1/datasources", headers=h("full")).json()] == ["store"]
    assert c.get("/v1/datasources/hr/schema", headers=h("full")).status_code == 404


def test_generations_are_private_to_their_consumer(make_client, fake):
    c = make_client()
    fake.push(ok("SELECT name FROM products"))
    gen = c.post("/v1/sql/generate", json={"datasource": "store", "question": "q"}, headers=h("full")).json()
    assert c.get(f"/v1/generations/{gen['generation_id']}", headers=h("gen_only")).status_code == 404
    r = c.post("/v1/sql/execute", json={"generation_id": gen["generation_id"]}, headers=h("other_ds"))
    assert r.status_code == 404


def test_schema_endpoint_is_policy_filtered(make_client):
    r = make_client().get("/v1/datasources/store/schema", headers=h("full"))
    assert r.status_code == 200
    text = r.text
    assert "customers" in text and "email" not in text and "internal_audit_log" not in text


def test_validate_endpoint(make_client):
    c = make_client()
    r = c.post(
        "/v1/sql/validate", json={"datasource": "store", "sql": "DELETE FROM orders"}, headers=h("full")
    )
    assert r.status_code == 200 and r.json()["valid"] is False
    assert r.json()["errors"][0]["code"] == "unsafe_sql"


def test_generation_failed_returns_422_with_attempts(make_client, fake):
    c = make_client()
    fake.push(*[ok("DROP TABLE orders")] * 3)
    r = c.post("/v1/sql/generate", json={"datasource": "store", "question": "q"}, headers=h("full"))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "generation_failed" and len(err["details"]["attempts"]) == 3


def test_request_validation_is_400(make_client):
    r = make_client().post("/v1/sql/generate", json={"datasource": "store"}, headers=h("full"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_rate_limit(make_client, fake):
    c = make_client(rate_limit=2)
    codes = [c.get("/v1/datasources", headers=h("full")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = c.get("/v1/datasources", headers=h("full"))
    assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) >= 1
    assert c.get("/v1/datasources", headers=h("gen_only")).status_code == 200  # per consumer


def test_llm_failure_is_502(make_client, fake):
    r = make_client().post(
        "/v1/sql/generate", json={"datasource": "store", "question": "q"}, headers=h("full")
    )
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_error"


def test_auth_disabled_mode(make_client, fake):
    c = make_client(auth=False)
    fake.push(ok("SELECT name FROM products"))
    r = c.post("/v1/sql/generate", json={"datasource": "hr", "question": "q"})
    assert r.status_code == 200


def test_python_client(make_client, fake):
    tc = make_client()
    client = SQLSentryClient("http://testserver", api_key=KEYS["full"])
    client._http = tc
    tc.headers.update(h("full"))
    fake.push(ok("SELECT COUNT(*) AS n FROM orders"))
    gen = client.generate("store", "how many orders")
    assert client.execute(gen["generation_id"])["rows"] == [[900]]
    assert client.validate("store", "SELECT name FROM products")["valid"]
    with pytest.raises(SQLSentryAPIError) as e:
        client.schema("hr")
    assert e.value.http_status == 404 and e.value.code == "datasource_not_found"


def test_openapi_declares_bearer_auth(make_client):
    spec = make_client().get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"


def test_root_redirects_to_docs(make_client):
    r = make_client().get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_doubled_bearer_prefix_is_tolerated(make_client):
    c = make_client()
    r = c.get("/v1/datasources", headers={"Authorization": f"Bearer Bearer {KEYS['full']}"})
    assert r.status_code == 200

import pytest

from sqlsentry import SQLSentry
from sqlsentry.config import DataSourceConfig, LLMSettings, ProviderConfig, Settings, StoreSettings
from sqlsentry.errors import (
    BadRequest,
    ExecutionError,
    GenerationFailed,
    GenerationNotFound,
    InvalidSQLError,
    PermissionDenied,
)
from sqlsentry.execution.executor import run_query
from sqlsentry.llm import FakeProvider
from sqlsentry.store import MemoryStore, SQLStore


def answer(sql="", explanation="ok", **kw):
    return {
        "sql": sql,
        "explanation": explanation,
        "tables_used": kw.get("tables_used", []),
        "assumptions": kw.get("assumptions", []),
        "needs_clarification": kw.get("needs_clarification", False),
        "clarification_question": kw.get("clarification_question"),
    }


TOP_CUSTOMERS = """-- Top 5 customers by revenue
SELECT c.name, SUM(oi.quantity * oi.unit_price) AS revenue
FROM customers c
JOIN orders o ON o.customer_id = c.id  -- each order's buyer
JOIN order_items oi ON oi.order_id = o.id
GROUP BY c.name
ORDER BY revenue DESC
LIMIT 5"""


@pytest.fixture
def fake():
    return FakeProvider(name="fake")


@pytest.fixture
def make_sentry(sample_db_url, store_policy, fake):
    def _make(policy=None, **engine_kw):
        settings = Settings(
            datasources={
                "store": DataSourceConfig(
                    url=sample_db_url,
                    description="Fictional online store",
                    policy=policy or store_policy,
                    glossary=["revenue = order_items.quantity * order_items.unit_price"],
                )
            },
            llm=LLMSettings(default="fake", providers={"fake": ProviderConfig(type="fake")}),
            store=StoreSettings(url=None),
            engine=engine_kw,
        )
        return SQLSentry(settings, providers={"fake": fake}, store=MemoryStore())

    return _make


def test_generate_happy_path(make_sentry, fake):
    fake.push(answer(TOP_CUSTOMERS, "Sums each customer's order value and ranks them."))
    gen = make_sentry().generate("store", "top 5 customers by revenue", consumer="team-a")
    assert gen.status == "ok"
    assert "Top 5 customers by revenue" in gen.sql and "LIMIT 5" in gen.sql
    assert gen.tables_used == ["customers", "orders", "order_items"]
    assert gen.explanation.startswith("Sums")
    assert len(gen.attempts) == 1 and gen.attempts[0].stage == "ok"
    assert gen.provider == "fake" and gen.policy_fingerprint and gen.schema_fingerprint
    assert gen.usage.input_tokens > 0


def test_prompt_never_contains_hidden_data(make_sentry, fake):
    fake.push(answer("SELECT name FROM customers"))
    make_sentry().generate("store", "list customers")
    sent = fake.calls[0]["system"] + fake.calls[0]["messages"][0]["content"]
    assert "email" not in sent and "internal_audit_log" not in sent
    assert "revenue = order_items.quantity" in sent  # glossary included


def test_repair_after_hidden_column(make_sentry, fake):
    fake.push(answer("SELECT name, email FROM customers"), answer("SELECT name FROM customers"))
    gen = make_sentry().generate("store", "customer names and emails")
    assert gen.status == "ok" and "email" not in gen.sql.lower()
    assert [a.stage for a in gen.attempts] == ["guard", "ok"]
    repair_turn = fake.calls[1]["messages"][-1]["content"]
    assert "email" in repair_turn and "rejected" in repair_turn


def test_repair_after_unsafe_sql(make_sentry, fake):
    fake.push(answer("DELETE FROM orders"), answer("SELECT COUNT(*) AS n FROM orders"))
    gen = make_sentry().generate("store", "how many orders")
    assert gen.status == "ok" and gen.attempts[0].stage == "guard"


def test_repair_after_unparseable_response(make_sentry, fake):
    fake.push("I think you want the products table", answer("SELECT name FROM products"))
    gen = make_sentry().generate("store", "products")
    assert [a.stage for a in gen.attempts] == ["parse", "ok"]


def test_repair_after_database_rejects_plan(make_sentry, fake):
    fake.push(answer("SELECT no_such_function(name) FROM products"), answer("SELECT name FROM products"))
    gen = make_sentry().generate("store", "products")
    assert [a.stage for a in gen.attempts] == ["verify", "ok"]
    assert "no_such_function" in gen.attempts[0].error.lower()


def test_clarification(make_sentry, fake):
    fake.push(answer(needs_clarification=True, clarification_question="Revenue in which year?"))
    gen = make_sentry().generate("store", "revenue")
    assert gen.status == "needs_clarification" and gen.sql is None
    assert gen.clarification_question == "Revenue in which year?"


def test_gives_up_after_max_attempts(make_sentry, fake):
    fake.push(*[answer("DROP TABLE orders")] * 2)
    sentry = make_sentry(max_attempts=2)
    with pytest.raises(GenerationFailed) as exc:
        sentry.generate("store", "drop it")
    gen_id = exc.value.details["generation_id"]
    assert len(exc.value.details["attempts"]) == 2
    assert sentry.get_generation(gen_id).status == "failed"


def test_bad_questions(make_sentry):
    with pytest.raises(BadRequest):
        make_sentry().generate("store", "   ")
    with pytest.raises(BadRequest):
        make_sentry().generate("store", "x" * 5000)


def test_provider_must_be_allowed_by_policy(make_sentry, store_policy, fake):
    policy = store_policy.model_copy(update={"allowed_providers": ["local-*"]})
    with pytest.raises(PermissionDenied):
        make_sentry(policy).generate("store", "anything")
    assert fake.calls == []  # nothing was sent anywhere


def test_execute_returns_rows(make_sentry, fake):
    fake.push(answer(TOP_CUSTOMERS))
    sentry = make_sentry()
    gen = sentry.generate("store", "top customers")
    res = sentry.execute(gen.id)
    assert res.columns == ["name", "revenue"]
    assert res.row_count == 5 and not res.truncated
    assert res.rows[0][1] >= res.rows[-1][1]


def test_execute_respects_row_cap(make_sentry, fake):
    fake.push(answer("SELECT id FROM orders"))
    sentry = make_sentry()
    res = sentry.execute(sentry.generate("store", "all orders"), max_rows=7)
    assert res.row_count == 7


def test_execute_disabled_by_policy(make_sentry, store_policy, fake):
    fake.push(answer("SELECT name FROM products"))
    sentry = make_sentry(store_policy.model_copy(update={"allow_execute": False}))
    gen = sentry.generate("store", "products")
    with pytest.raises(PermissionDenied):
        sentry.execute(gen)


def test_execute_rechecks_current_policy(make_sentry, store_policy, fake):
    fake.push(answer("SELECT name, country FROM customers"))
    sentry = make_sentry()
    gen = sentry.generate("store", "where are customers")
    # Admin hides 'country' after the SQL was generated.
    sentry.settings.datasources["store"].policy.columns.hidden.append("customers.country")
    sentry.refresh("store")
    with pytest.raises(InvalidSQLError):
        sentry.execute(gen)


def test_cannot_execute_failed_or_foreign_generation(make_sentry, fake):
    fake.push(answer("SELECT name FROM products"))
    sentry = make_sentry()
    gen = sentry.generate("store", "products", consumer="team-a")
    with pytest.raises(GenerationNotFound):
        sentry.execute(gen.id, consumer="team-b")


def test_validate(make_sentry):
    sentry = make_sentry()
    ok = sentry.validate("store", "SELECT name FROM products")
    assert ok.valid and "LIMIT" in ok.sql
    bad = sentry.validate("store", "SELECT email FROM customers")
    assert not bad.valid and bad.errors[0].code == "invalid_sql"


def test_feedback(make_sentry, fake):
    fake.push(answer("SELECT name FROM products"))
    sentry = make_sentry()
    gen = sentry.generate("store", "products", consumer="a")
    fb = sentry.feedback(
        gen.id, "incorrect", corrected_sql="SELECT name FROM products ORDER BY 1", consumer="a"
    )
    assert sentry.store.list_feedback()[0].id == fb.id
    with pytest.raises(BadRequest):
        sentry.feedback(gen.id, "meh", consumer="a")


def test_database_level_read_only_even_without_guard(sample_db_url):
    # Defence in depth: the executor itself refuses writes on SQLite.
    from sqlalchemy import create_engine

    engine = create_engine(sample_db_url)
    with pytest.raises(ExecutionError):
        run_query(engine, "DELETE FROM orders", dialect="sqlite", timeout_s=5, max_rows=10)
    cols, rows, _, _ = run_query(
        engine, "SELECT COUNT(*) FROM orders", dialect="sqlite", timeout_s=5, max_rows=1
    )
    assert rows[0][0] == 900
    engine.dispose()


def test_statement_timeout(sample_db_url):
    from sqlalchemy import create_engine

    engine = create_engine(sample_db_url)
    slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    with pytest.raises(ExecutionError):
        run_query(engine, slow, dialect="sqlite", timeout_s=1, max_rows=1)
    engine.dispose()


def test_sql_store_round_trip(tmp_path, make_sentry, fake):
    store = SQLStore(f"sqlite:///{(tmp_path / 'h' / 'history.db').as_posix()}")
    fake.push(answer("SELECT name FROM products"))
    sentry = make_sentry()
    sentry.store = store
    gen = sentry.generate("store", "products")
    assert store.get_generation(gen.id).sql == gen.sql
    sentry.execute(gen)
    sentry.feedback(gen.id, "correct")
    assert store.list_feedback()[0].rating == "correct"
    store.close()

"""Runs only when SQLSENTRY_TEST_PG_URL is set (CI provides a Postgres service)."""

import os

import pytest
from sqlalchemy import create_engine, text

from sqlsentry import SQLSentry
from sqlsentry.config import DataSourceConfig, LLMSettings, ProviderConfig, Settings, StoreSettings
from sqlsentry.errors import ExecutionError
from sqlsentry.execution.executor import run_query
from sqlsentry.llm import FakeProvider
from sqlsentry.policy import ColumnPolicy, Policy, TablePolicy
from sqlsentry.store import MemoryStore

PG_URL = os.environ.get("SQLSENTRY_TEST_PG_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not PG_URL, reason="SQLSENTRY_TEST_PG_URL not set"),
]
SCHEMA = "sqlsentry_test"


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(PG_URL)
    with engine.begin() as c:
        c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        c.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        c.execute(text(f"CREATE TABLE {SCHEMA}.customers (id int primary key, name text, email text)"))
        c.execute(text(f"COMMENT ON TABLE {SCHEMA}.customers IS 'People who bought something'"))
        c.execute(text(f"COMMENT ON COLUMN {SCHEMA}.customers.name IS 'Full name'"))
        c.execute(
            text(
                f"CREATE TABLE {SCHEMA}.orders (id int primary key, "
                f"customer_id int references {SCHEMA}.customers(id), total numeric(10,2))"
            )
        )
        c.execute(
            text(f"INSERT INTO {SCHEMA}.customers VALUES (1,'Asha Rao','a@x.test'),(2,'Ben Li','b@x.test')")
        )
        c.execute(text(f"INSERT INTO {SCHEMA}.orders VALUES (1,1,10.50),(2,1,5.25),(3,2,99.99)"))
    yield engine
    with engine.begin() as c:
        c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    engine.dispose()


@pytest.fixture
def sentry(pg_engine):
    fake = FakeProvider(name="fake")
    settings = Settings(
        datasources={
            "pg": DataSourceConfig(
                url=PG_URL,
                db_schema=SCHEMA,
                policy=Policy(
                    tables=TablePolicy(include=["*"]),
                    columns=ColumnPolicy(hidden=["customers.email"]),
                    allow_execute=True,
                ),
            )
        },
        llm=LLMSettings(default="fake", providers={"fake": ProviderConfig(type="fake")}),
        store=StoreSettings(url=None),
    )
    s = SQLSentry(settings, providers={"fake": fake}, store=MemoryStore())
    s.fake = fake
    yield s
    s.close()


def test_introspection_reads_comments_and_hides_columns(sentry):
    cat = sentry.schema("pg")
    customers = cat.table("customers")
    assert customers.comment == "People who bought something"
    assert customers.column("name").comment == "Full name"
    assert customers.column("email") is None
    assert cat.table("orders").foreign_keys[0].ref_table == "customers"


def test_generate_and_execute_with_like(sentry):
    sql = (
        f"-- Customers whose name starts with A\nSELECT c.name, SUM(o.total) AS spent "
        f"FROM {SCHEMA}.customers c JOIN {SCHEMA}.orders o ON o.customer_id = c.id "
        "WHERE c.name LIKE 'A%' GROUP BY c.name"
    )
    sentry.fake.push({"sql": sql, "explanation": "x"})
    gen = sentry.generate("pg", "spend of customers starting with A")
    assert gen.status == "ok", gen.attempts
    res = sentry.execute(gen)
    assert res.rows == [["Asha Rao", 15.75]]


def test_transaction_is_read_only(pg_engine):
    with pytest.raises(ExecutionError):
        run_query(pg_engine, f"DELETE FROM {SCHEMA}.orders", dialect="postgres", timeout_s=5, max_rows=1)


def test_statement_timeout(pg_engine):
    with pytest.raises(ExecutionError):
        run_query(pg_engine, "SELECT pg_sleep(3)", dialect="postgres", timeout_s=1, max_rows=1)

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from sqlsentry.demo import create_sample_db
from sqlsentry.policy import ColumnPolicy, Policy, TablePolicy
from sqlsentry.schema.introspect import introspect


@pytest.fixture(scope="session")
def sample_db_path(tmp_path_factory) -> Path:
    return create_sample_db(tmp_path_factory.mktemp("db") / "store.db")


@pytest.fixture(scope="session")
def sample_db_url(sample_db_path) -> str:
    return f"sqlite:///{sample_db_path.as_posix()}"


@pytest.fixture
def store_policy() -> Policy:
    return Policy(
        tables=TablePolicy(include=["*"], exclude=["internal_*"]),
        columns=ColumnPolicy(hidden=["customers.email"]),
        max_rows=100,
        allow_execute=True,
    )


@pytest.fixture
def store_catalog(sample_db_url, store_policy):
    engine = create_engine(sample_db_url)
    try:
        return introspect(engine, datasource="store", dialect="sqlite", policy=store_policy)
    finally:
        engine.dispose()

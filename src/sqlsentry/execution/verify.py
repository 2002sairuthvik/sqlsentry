"""Dry-run verification: ask the database to plan the query without running it.

Catches real-world errors the static guard can't (type mismatches, dialect quirks).
Dialects whose EXPLAIN has side effects or needs special session state are skipped.
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from ..errors import ExecutionError
from .executor import _db_message, readonly_connection

EXPLAIN_PREFIX: dict[str, str] = {
    "sqlite": "EXPLAIN QUERY PLAN ",
    "postgres": "EXPLAIN ",
    "redshift": "EXPLAIN ",
    "mysql": "EXPLAIN ",
    "duckdb": "EXPLAIN ",
    "trino": "EXPLAIN ",
    "databricks": "EXPLAIN ",
    "clickhouse": "EXPLAIN ",
    "snowflake": "EXPLAIN USING TEXT ",
}
# Not supported on purpose: oracle (EXPLAIN PLAN writes to PLAN_TABLE), tsql (needs SHOWPLAN
# session state), bigquery (dry runs go through the API, not SQL).


def supports_dry_run(dialect: str) -> bool:
    return dialect in EXPLAIN_PREFIX


def dry_run(engine: Engine, sql: str, *, dialect: str, timeout_s: int) -> None:
    """Raise ExecutionError if the database rejects the query plan."""
    with readonly_connection(engine, dialect, timeout_s) as conn:
        try:
            conn.exec_driver_sql(EXPLAIN_PREFIX[dialect] + sql).fetchall()
        except SQLAlchemyError as e:
            raise ExecutionError(_db_message(e)) from e

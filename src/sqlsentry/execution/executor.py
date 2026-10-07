"""Read-only execution.

The guard already guarantees a single read-only SELECT; this layer adds database-side
protection where the dialect supports it (read-only transaction, statement timeout) and
always rolls back. Defence in depth still requires a read-only database user.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import math
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from ..errors import ExecutionError


@contextmanager
def readonly_connection(engine: Engine, dialect: str, timeout_s: int) -> Iterator[Connection]:
    try:
        with engine.connect() as conn:  # SQLAlchemy 2.0 auto-begins on the first statement
            # Execute SQL text exactly as written: without this, drivers such as psycopg and
            # pymysql treat '%' (e.g. in LIKE 'A%') as a parameter placeholder.
            conn = conn.execution_options(no_parameters=True)
            try:
                _session_guards(conn, dialect, timeout_s)
                yield conn
            finally:
                if conn.in_transaction():
                    conn.rollback()
                if dialect == "sqlite":
                    conn.connection.dbapi_connection.set_progress_handler(None, 0)
    except SQLAlchemyError as e:
        raise ExecutionError(f"Database error: {_db_message(e)}") from e


def _session_guards(conn: Connection, dialect: str, timeout_s: int) -> None:
    ms = int(timeout_s * 1000)
    if dialect in ("postgres", "redshift"):
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")  # first statement of the transaction
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {ms}")
    elif dialect == "mysql":
        conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {ms}")
        conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
    elif dialect == "sqlite":
        raw = conn.connection.dbapi_connection
        conn.exec_driver_sql("PRAGMA query_only = ON")
        deadline = time.monotonic() + timeout_s
        raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)


def run_query(
    engine: Engine, sql: str, *, dialect: str, timeout_s: int, max_rows: int
) -> tuple[list[str], list[list[Any]], bool, int]:
    """Return (columns, rows, truncated, duration_ms)."""
    start = time.perf_counter()
    with readonly_connection(engine, dialect, timeout_s) as conn:
        try:
            result = conn.exec_driver_sql(sql)
            columns = list(result.keys())
            fetched = result.fetchmany(max_rows + 1)
        except SQLAlchemyError as e:
            raise ExecutionError(f"Database error: {_db_message(e)}") from e
    truncated = len(fetched) > max_rows
    rows = [[to_jsonable(v) for v in row] for row in fetched[:max_rows]]
    return columns, rows, truncated, int((time.perf_counter() - start) * 1000)


def to_jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, decimal.Decimal):
        return float(v) if v.is_finite() else str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(v)).decode()
    return str(v)


def _db_message(e: SQLAlchemyError) -> str:
    orig = getattr(e, "orig", None)
    text = str(orig if orig is not None else e).strip()
    return text.splitlines()[0][:500] if text else type(e).__name__

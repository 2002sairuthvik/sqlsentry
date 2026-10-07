"""Layer 1: everything here must be rejected regardless of policy."""

import pytest

from sqlsentry.errors import InvalidSQLError, UnsafeSQLError
from sqlsentry.guard import check_sql
from sqlsentry.guard.baseline import check_baseline, parse_single
from sqlsentry.policy import Policy, TablePolicy

OPEN = Policy(tables=TablePolicy(include=["*"]), max_rows=1_000_000)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (id) VALUES (1)",
        "UPDATE orders SET status = 'x'",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "TRUNCATE TABLE orders",
        "ALTER TABLE orders ADD COLUMN x INT",
        "CREATE TABLE x AS SELECT * FROM orders",
        "GRANT ALL ON orders TO public",
        "MERGE INTO orders USING products ON 1=1 WHEN MATCHED THEN DELETE",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "BEGIN",
        "VACUUM",
    ],
)
def test_non_select_statements_rejected(sql, store_catalog):
    with pytest.raises((UnsafeSQLError, InvalidSQLError)):
        check_sql(sql, catalog=store_catalog, policy=OPEN, dialect="sqlite")


def test_stacked_statements_rejected(store_catalog):
    with pytest.raises(UnsafeSQLError, match="Exactly one"):
        check_sql("SELECT 1; DROP TABLE orders", catalog=store_catalog, policy=OPEN, dialect="sqlite")


def test_write_inside_cte_rejected():
    stmt = parse_single("WITH d AS (DELETE FROM orders RETURNING id) SELECT * FROM d", "postgres")
    with pytest.raises(UnsafeSQLError):
        check_baseline(stmt)


@pytest.mark.parametrize(
    "sql,dialect",
    [
        ("SELECT pg_sleep(10)", "postgres"),
        ("SELECT pg_read_file('/etc/passwd')", "postgres"),
        ("SELECT * FROM dblink('host=x', 'select 1') AS t(a int)", "postgres"),
        ("SELECT query_to_xml('delete from orders', true, true, '')", "postgres"),
        ("SELECT set_config('statement_timeout', '0', false)", "postgres"),
        ("SELECT SLEEP(5)", "mysql"),
        ("SELECT BENCHMARK(100000000, MD5('x'))", "mysql"),
        ("SELECT LOAD_FILE('/etc/passwd')", "mysql"),
        ("SELECT xp_cmdshell('dir')", "tsql"),
        ("SELECT * FROM read_csv('/etc/passwd')", "duckdb"),
        ("SELECT load_extension('evil.so')", "sqlite"),
    ],
)
def test_dangerous_functions_rejected(sql, dialect):
    with pytest.raises(UnsafeSQLError):
        check_baseline(parse_single(sql, dialect))


def test_select_into_rejected():
    with pytest.raises(UnsafeSQLError):
        check_baseline(parse_single("SELECT * INTO backup FROM orders", "tsql"))


def test_row_locking_rejected():
    with pytest.raises(UnsafeSQLError):
        check_baseline(parse_single("SELECT id FROM orders FOR UPDATE", "postgres"))


@pytest.mark.parametrize("sql", ["", "   ", "SELEC id FROM", "SELECT (("])
def test_garbage_is_invalid(sql):
    with pytest.raises(InvalidSQLError):
        parse_single(sql, "sqlite")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT COUNT(*) FROM orders",
    ],
)
def test_read_only_queries_pass_baseline(sql):
    check_baseline(parse_single(sql, "sqlite"))

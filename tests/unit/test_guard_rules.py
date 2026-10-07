"""Layer 2: policy-driven checks, rewrites and output formatting."""

import pytest
import sqlglot

from sqlsentry.errors import InvalidSQLError, UnsafeSQLError
from sqlsentry.guard import check_sql


def guard(sql, catalog, policy, dialect="sqlite", **kw):
    return check_sql(sql, catalog=catalog, policy=policy, dialect=dialect, **kw)


def test_valid_query_passes_and_reports_tables(store_catalog, store_policy):
    r = guard(
        "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS revenue "
        "FROM customers c JOIN orders o ON o.customer_id = c.id "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY c.name ORDER BY revenue DESC LIMIT 5",
        store_catalog,
        store_policy,
    )
    assert r.tables == ["customers", "orders", "order_items"]
    assert "LIMIT 5" in r.sql
    assert r.warnings == []


def test_hidden_column_rejected(store_catalog, store_policy):
    with pytest.raises(InvalidSQLError, match="email"):
        guard("SELECT name, email FROM customers", store_catalog, store_policy)


def test_hidden_column_rejected_even_with_full_catalog(store_catalog, store_policy):
    # The guard re-applies the policy itself, so a caller passing an unfiltered catalog can't leak.
    from sqlsentry.schema.models import Column

    leaky = store_catalog.model_copy(deep=True)
    leaky.table("customers").columns.append(Column(name="email", type="TEXT"))
    with pytest.raises(InvalidSQLError):
        guard("SELECT email FROM customers", leaky, store_policy)


def test_excluded_table_rejected_like_unknown(store_catalog, store_policy):
    with pytest.raises(InvalidSQLError, match="Unknown or not permitted table"):
        guard("SELECT * FROM internal_audit_log", store_catalog, store_policy)
    with pytest.raises(InvalidSQLError, match="Unknown or not permitted table"):
        guard("SELECT * FROM does_not_exist", store_catalog, store_policy)


def test_system_catalogs_rejected(store_catalog, store_policy):
    with pytest.raises(InvalidSQLError):
        guard("SELECT sql FROM sqlite_master", store_catalog, store_policy)
    with pytest.raises((InvalidSQLError, UnsafeSQLError)):
        guard("SELECT * FROM information_schema.tables", store_catalog, store_policy, dialect="postgres")


def test_unknown_column_rejected(store_catalog, store_policy):
    with pytest.raises(InvalidSQLError):
        guard("SELECT customer_name FROM customers", store_catalog, store_policy)


def test_star_is_expanded_without_hidden_columns(store_catalog, store_policy):
    r = guard("SELECT * FROM customers", store_catalog, store_policy)
    assert "email" not in r.sql.lower()
    assert "country" in r.sql


def test_qualified_star_is_expanded(store_catalog, store_policy):
    r = guard("SELECT c.* FROM customers c", store_catalog, store_policy)
    assert "email" not in r.sql.lower()


def test_count_star_is_fine(store_catalog, store_policy):
    r = guard("SELECT COUNT(*) AS n FROM orders", store_catalog, store_policy)
    assert "COUNT(*)" in r.sql


def test_cte_names_are_allowed(store_catalog, store_policy):
    r = guard(
        "WITH big AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) "
        "SELECT customer_id FROM big WHERE n > 10",
        store_catalog,
        store_policy,
    )
    assert r.tables == ["orders"]


def test_limit_added_when_missing(store_catalog, store_policy):
    r = guard("SELECT name FROM products", store_catalog, store_policy)
    assert "LIMIT 100" in r.sql
    assert any("LIMIT 100 added" in w for w in r.warnings)


def test_limit_clamped_when_too_high(store_catalog, store_policy):
    r = guard("SELECT name FROM products LIMIT 5000", store_catalog, store_policy)
    assert "LIMIT 100" in r.sql and "5000" not in r.sql


def test_limit_kept_when_within_cap(store_catalog, store_policy):
    r = guard("SELECT name FROM products LIMIT 10", store_catalog, store_policy)
    assert "LIMIT 10" in r.sql and r.warnings == []


def test_limit_on_union(store_catalog, store_policy):
    r = guard("SELECT name FROM products UNION SELECT name FROM customers", store_catalog, store_policy)
    assert r.sql.rstrip().endswith("LIMIT 100")


def test_request_max_rows_cannot_exceed_policy(store_catalog, store_policy):
    r = guard("SELECT name FROM products", store_catalog, store_policy, max_rows=10_000)
    assert "LIMIT 100" in r.sql


def test_comments_are_preserved(store_catalog, store_policy):
    r = guard("-- Top products by price\nSELECT name FROM products ORDER BY unit_price DESC LIMIT 3",
              store_catalog, store_policy)  # fmt: skip
    assert "Top products by price" in r.sql


def test_output_is_dialect_correct(store_catalog, store_policy):
    r = guard("SELECT name FROM products LIMIT 3", store_catalog, store_policy, dialect="tsql")
    assert "TOP" in r.sql.upper() or "FETCH" in r.sql.upper()


def test_output_round_trips(store_catalog, store_policy):
    r = guard("SELECT category, AVG(unit_price) FROM products GROUP BY category", store_catalog, store_policy)
    assert sqlglot.parse_one(r.sql, read="sqlite") is not None


def test_cross_database_reference_rejected(store_catalog, store_policy):
    with pytest.raises((UnsafeSQLError, InvalidSQLError)):
        guard("SELECT * FROM otherdb.public.orders", store_catalog, store_policy, dialect="postgres")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM orders",
        "SELECT SUM(quantity * unit_price) AS revenue, AVG(quantity) AS q FROM order_items",
        "SELECT AVG(n) AS a FROM (SELECT order_id, COUNT(*) AS n FROM order_items GROUP BY order_id) AS t",
    ],
)
def test_single_row_aggregates_are_not_capped(store_catalog, store_policy, sql):
    r = guard(sql, store_catalog, store_policy)
    assert "LIMIT" not in r.sql.upper()
    assert r.warnings == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT status, COUNT(*) AS n FROM orders GROUP BY status",  # one row per group
        "SELECT COUNT(*) OVER () AS n FROM orders",  # window: one row per input row
        "SELECT (SELECT MAX(unit_price) FROM products) AS m FROM orders",  # scalar subquery per row
        "SELECT id, COUNT(*) OVER (PARTITION BY status) AS n FROM orders",
        "SELECT name FROM products",
        "SELECT COUNT(*) AS n FROM orders UNION ALL SELECT COUNT(*) AS n FROM products",  # set op
    ],
)
def test_multi_row_queries_keep_the_cap(store_catalog, store_policy, sql):
    r = guard(sql, store_catalog, store_policy)
    assert r.sql.rstrip().endswith("LIMIT 100")

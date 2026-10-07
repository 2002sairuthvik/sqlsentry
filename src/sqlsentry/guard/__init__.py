"""The SQL guard: every SQL string passes through :func:`check_sql` before it is returned
or executed, whether it came from an LLM, a user, or storage.

Layer 1 (``baseline``) is locked; Layer 2 (``rules``) applies the datasource policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..policy import Policy
from ..schema.models import SchemaCatalog
from .baseline import check_baseline, parse_single
from .rules import apply_limit, check_columns, check_tables


@dataclass
class GuardResult:
    sql: str
    tables: list[str]
    warnings: list[str] = field(default_factory=list)


def check_sql(
    sql: str,
    *,
    catalog: SchemaCatalog,
    policy: Policy,
    dialect: str,
    max_rows: int | None = None,
) -> GuardResult:
    """Return safe, policy-compliant SQL in ``dialect`` or raise
    :class:`~sqlsentry.errors.UnsafeSQLError` / :class:`~sqlsentry.errors.InvalidSQLError`."""
    catalog = catalog.filtered(policy)  # defence in depth: never trust the caller's catalog
    row_cap = min(max_rows, policy.max_rows) if max_rows else policy.max_rows

    stmt = parse_single(sql, dialect)
    check_baseline(stmt)
    tables = check_tables(stmt, catalog)
    stmt, warnings = check_columns(stmt, catalog, policy, dialect)
    stmt, limit_warnings = apply_limit(stmt, row_cap)
    check_baseline(stmt)  # rewrites must never produce something Layer 1 rejects

    return GuardResult(
        sql=stmt.sql(dialect=dialect, pretty=True, comments=True),
        tables=tables,
        warnings=warnings + limit_warnings,
    )


__all__ = ["GuardResult", "check_sql"]

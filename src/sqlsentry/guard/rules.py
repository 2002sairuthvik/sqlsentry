"""Layer 2: datasource policy checks on an already-baseline-safe statement.

* every table must be a CTE or a table in the policy-filtered catalog,
* every column must resolve against that catalog (hidden columns are absent from it),
* ``SELECT *`` is expanded to the visible columns so the database can't return hidden ones,
* the row count is capped at ``policy.max_rows``.

Unknown and forbidden tables get the same message so errors don't reveal what exists.
"""

from __future__ import annotations

import logging

from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify

from ..errors import InvalidSQLError, UnsafeSQLError
from ..policy import Policy
from ..schema.models import SchemaCatalog

log = logging.getLogger(__name__)


def check_tables(stmt: exp.Expression, catalog: SchemaCatalog) -> list[str]:
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    used: list[str] = []
    for t in stmt.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier) or not t.name:
            raise UnsafeSQLError("Table functions and dynamic table references are not allowed.")
        if t.args.get("catalog"):
            raise UnsafeSQLError(f"Cross-database reference '{t.sql()}' is not allowed.")
        if t.db and (catalog.db_schema is None or t.db.lower() != catalog.db_schema.lower()):
            raise InvalidSQLError(f"Unknown or not permitted table: {t.sql()}")
        if not t.db and t.name.lower() in cte_names:
            continue
        known = catalog.table(t.name)
        if known is None:
            available = ", ".join(catalog.table_names())
            raise InvalidSQLError(f"Unknown or not permitted table: {t.name}. Available tables: {available}")
        if known.name not in used:
            used.append(known.name)
    return used


def _uses_star(stmt: exp.Expression) -> bool:
    for sel in stmt.find_all(exp.Select):
        for e in sel.expressions:
            if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
                return True
    return False


def check_columns(
    stmt: exp.Expression, catalog: SchemaCatalog, policy: Policy, dialect: str
) -> tuple[exp.Expression, list[str]]:
    """Validate columns; return the statement to emit (star-expanded if needed) and warnings."""
    warnings: list[str] = []
    try:
        qualified = qualify(
            stmt.copy(),
            schema=catalog.sqlglot_schema(),
            dialect=dialect,
            quote_identifiers=False,
            identify=False,
            validate_qualify_columns=True,
        )
    except OptimizeError as e:
        raise InvalidSQLError(f"{e}. Use only columns listed in the schema.") from e
    except Exception as e:  # sqlglot can't qualify some dialect constructs
        log.debug("qualify failed: %r", e)
        qualified = None
        warnings.append("Column-level validation was partial for this query.")

    checked = qualified if qualified is not None else stmt
    aliases = {t.alias_or_name.lower(): t.name for t in checked.find_all(exp.Table)}
    for col in checked.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        table_name = aliases.get(col.table.lower()) if col.table else None
        candidates = [table_name] if table_name else list(aliases.values())
        if any(policy.column_hidden(t, col.name) for t in candidates):
            raise InvalidSQLError(f"Unknown or not permitted column: {col.name}")

    if _uses_star(stmt):
        if qualified is None:
            raise UnsafeSQLError("SELECT * could not be expanded safely here; list the columns explicitly.")
        return qualified, warnings
    return stmt, warnings


def apply_limit(stmt: exp.Expression, max_rows: int) -> tuple[exp.Expression, list[str]]:
    if not isinstance(stmt, exp.Query):
        return stmt, []
    limit = stmt.args.get("limit")
    if limit is None:
        return stmt.limit(max_rows), [f"LIMIT {max_rows} added (policy row cap)."]

    count = limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.expression
    if isinstance(count, exp.Literal) and count.is_int and int(count.this) <= max_rows:
        return stmt, []
    new_count = exp.Literal.number(max_rows)
    if isinstance(limit, exp.Fetch):
        limit.set("count", new_count)
    else:
        limit.set("expression", new_count)
    return stmt, [f"LIMIT reduced to {max_rows} (policy row cap)."]

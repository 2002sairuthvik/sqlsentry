"""Layer 1: the locked baseline.

These rules are hard-coded on purpose. There is no setting, flag or policy that turns
them off, in any deployment mode. Every statement must be:

* exactly one statement,
* a read-only query (SELECT / WITH ... SELECT / set operations of those),
* free of write, DDL, DCL, transaction, session or procedural nodes anywhere in the tree,
* free of row locking and SELECT ... INTO,
* free of functions that sleep, touch files/OS/network, run dynamic SQL, or control the server.

Anything ambiguous is rejected: false positives cost a repair attempt; false negatives
cost a breach.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from ..errors import InvalidSQLError, UnsafeSQLError

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
    "Command", "Grant", "Revoke", "Transaction", "Commit", "Rollback", "Set", "Use",
    "Copy", "Pragma", "LoadData", "Into", "Lock", "Analyze", "Kill", "Cache", "Uncache",
    "Refresh", "Attach", "Detach", "Execute", "Describe", "Show",
)  # fmt: skip
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n)
)

DENIED_FUNCTIONS = frozenset(
    {
        # sleeping / resource abuse
        "sleep", "benchmark", "waitfor", "get_lock", "release_lock",
        # file system, OS, network
        "load_file", "readfile", "writefile", "edit", "load_extension", "fts3_tokenizer",
        "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
        "read_text", "read_blob", "read_ndjson", "glob", "sniff_csv",
        "sys_exec", "sys_eval", "external_query",
        # dynamic SQL executed from a string (bypasses the guard)
        "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema",
        "cursor_to_xml", "table_to_xml", "database_to_xml", "schema_to_xml",
        "openrowset", "opendatasource", "openquery", "openxml", "exec", "eval",
        # server control / settings
        "set_config", "current_setting",
    }
)  # fmt: skip

DENIED_FUNCTION_PREFIXES = ("pg_", "dblink", "lo_", "xp_", "sp_", "dbms_", "utl_", "sys_", "system$")

ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.SetOperation, exp.Subquery)


def parse_single(sql: str, dialect: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise InvalidSQLError("SQL is empty.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (ParseError, TokenError) as e:
        raise InvalidSQLError(f"SQL does not parse as {dialect}: {_first_line(e)}") from e
    if len(statements) != 1:
        raise UnsafeSQLError(f"Exactly one SQL statement is allowed; got {len(statements)}.")
    return statements[0]


def function_name(node: exp.Func) -> str:
    name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
    return (name or "").lower()


def check_baseline(stmt: exp.Expression) -> None:
    if not isinstance(stmt, ALLOWED_ROOTS):
        raise UnsafeSQLError(f"Only read-only SELECT queries are allowed (got {stmt.key.upper()}).")
    if isinstance(stmt, exp.Subquery) and not isinstance(stmt.unnest(), (exp.Select, exp.SetOperation)):
        raise UnsafeSQLError("Only read-only SELECT queries are allowed.")

    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQLError(f"'{node.key.upper()}' is not allowed: queries must be read-only.")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise UnsafeSQLError("Row locking (FOR UPDATE / FOR SHARE) is not allowed.")
        if isinstance(node, exp.Func):
            name = function_name(node)
            if name in DENIED_FUNCTIONS or name.startswith(DENIED_FUNCTION_PREFIXES):
                raise UnsafeSQLError(f"Function '{name}' is not allowed.")


def _first_line(e: Exception) -> str:
    return str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__

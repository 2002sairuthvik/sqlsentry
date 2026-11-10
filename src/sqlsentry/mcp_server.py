"""MCP server: let AI agents query databases through sqlsentry's guard.

    sqlsentry-mcp --config /absolute/path/to/sqlsentry.yaml        # stdio transport

Agents can work in two ways:

* **The agent writes SQL** (``describe_schema`` then ``run_sql`` / ``validate_sql``).
  sqlsentry acts as a guarded database gateway; no LLM configuration is needed.
* **The agent delegates** (``generate_sql`` / ``ask``), using sqlsentry's own model, glossary
  and repair loop. Offered only when an LLM provider is configured.

Every query passes the same locked baseline and datasource policy as the CLI and REST API:
read-only, one statement, hidden tables and columns invisible and unqueryable, row cap, timeout.
Requires the ``mcp`` extra: ``pip install "sqlsentry[mcp]"``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import __version__
from .config import FewShotExample
from .engine import SQLSentry
from .errors import SQLSentryError
from .prompts import render_schema

log = logging.getLogger("sqlsentry.mcp")

DEFAULT_MAX_ROWS = 50
MAX_ROWS_LIMIT = 1000

INSTRUCTIONS = """\
sqlsentry gives you safe, read-only access to databases.

Workflow:
1. list_datasources to see what you can query.
2. describe_schema for the datasource you need: it shows every table and column you may use,
   plus business definitions (glossary) and verified example queries. Anything not listed is
   hidden by policy and cannot be queried.
3. Write one read-only SELECT and call run_sql (or validate_sql to check it first).
   If a query is rejected, the error says exactly why; fix it and try again.
4. If generate_sql / ask are available, you can instead let sqlsentry write the SQL.

Every query is checked by a guard: single read-only SELECT statements only, no hidden tables or
columns, a row limit is enforced. Results may be truncated; say so when reporting them.
"""


# ---------------------------------------------------------------- result models


class DatasourceSummary(BaseModel):
    name: str
    description: str
    dialect: str
    mode: Literal["connected", "schema_only"]
    can_execute: bool = Field(description="Whether run_sql / ask can run queries on it")


class SchemaDescription(BaseModel):
    datasource: str
    dialect: str
    can_execute: bool
    schema_text: str = Field(description="Tables, columns, types, keys, comments and sample values")
    glossary: list[str] = Field(description="Business definitions to follow when writing SQL")
    examples: list[FewShotExample] = Field(description="Verified question -> SQL examples")
    note: str = "Only the tables and columns listed are available; everything else is hidden by policy."


class SQLCheck(BaseModel):
    valid: bool
    sql: str | None = Field(None, description="The query as it would run, after the guard's rewrites")
    tables: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []


class QueryResult(BaseModel):
    datasource: str
    sql: str = Field(description="The query exactly as it was run")
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = Field(description="True if more rows exist than were returned")
    warnings: list[str] = []


class GeneratedSQL(BaseModel):
    status: Literal["ok", "needs_clarification", "failed"]
    sql: str | None
    explanation: str | None
    assumptions: list[str]
    warnings: list[str]
    clarification_question: str | None
    generation_id: str


class Answer(GeneratedSQL):
    result: QueryResult | None = Field(None, description="Rows, unless clarification is needed")


# ---------------------------------------------------------------- server


def _fail(e: SQLSentryError):
    from mcp.server.mcpserver.exceptions import ToolError

    return ToolError(f"{e.code}: {e.message}")


def _rows_cap(max_rows: int) -> int:
    return max(1, min(int(max_rows), MAX_ROWS_LIMIT))


def build_server(sentry: SQLSentry):
    """Create the MCP server for an existing engine (also used by tests)."""
    from mcp.server.mcpserver import MCPServer
    from mcp_types import ToolAnnotations

    read_only = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )
    server = MCPServer(
        "sqlsentry",
        title="sqlsentry",
        description="Safe, read-only database access for agents, enforced by a SQL guard.",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url="https://github.com/2002sairuthvik/sqlsentry",
    )

    @server.tool(annotations=read_only)
    def list_datasources() -> list[DatasourceSummary]:
        """List the databases you can query, with their SQL dialect and whether queries can run."""
        out = []
        for name in sentry.datasources():
            ds = sentry.datasource(name)
            out.append(
                DatasourceSummary(
                    name=name,
                    description=ds.description,
                    dialect=ds.resolved_dialect,
                    mode=ds.mode,
                    can_execute=ds.can_connect and ds.policy.allow_execute,
                )
            )
        return out

    @server.tool(annotations=read_only)
    def describe_schema(datasource: str) -> SchemaDescription:
        """Show the tables and columns of a datasource that you are allowed to query, plus business
        definitions and verified example queries. Call this before writing SQL."""
        try:
            ds = sentry.datasource(datasource)
            catalog = sentry.schema(datasource)
        except SQLSentryError as e:
            raise _fail(e) from e
        return SchemaDescription(
            datasource=datasource,
            dialect=ds.resolved_dialect,
            can_execute=ds.can_connect and ds.policy.allow_execute,
            schema_text=render_schema(catalog) or "(no tables are visible under the current policy)",
            glossary=ds.glossary,
            examples=ds.examples,
        )

    @server.tool(annotations=read_only)
    def validate_sql(datasource: str, sql: str) -> SQLCheck:
        """Check a SQL query against the guard and policy without running it. Returns the query as it
        would run (e.g. with a row limit added) or the reasons it is rejected."""
        try:
            r = sentry.validate(datasource, sql)
        except SQLSentryError as e:
            raise _fail(e) from e
        return SQLCheck(
            valid=r.valid,
            sql=r.sql,
            tables=r.tables,
            warnings=r.warnings,
            errors=[f"{i.code}: {i.message}" for i in r.errors],
        )

    @server.tool(annotations=read_only)
    def run_sql(datasource: str, sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> QueryResult:
        """Run one read-only SELECT on a datasource and return the rows. The query is checked by the
        guard first; rejected queries return an error explaining what to fix."""
        try:
            check = sentry.validate(datasource, sql)
            if not check.valid:
                from mcp.server.mcpserver.exceptions import ToolError

                raise ToolError(
                    "Query rejected: " + "; ".join(f"{i.code}: {i.message}" for i in check.errors)
                )
            result = sentry.execute_sql(datasource, sql, max_rows=_rows_cap(max_rows), consumer="mcp")
        except SQLSentryError as e:
            raise _fail(e) from e
        log.info("mcp run_sql datasource=%s rows=%d", datasource, result.row_count)
        return QueryResult(
            datasource=datasource,
            sql=result.sql or sql,
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            truncated=result.truncated,
            warnings=result.warnings,
        )

    if sentry.settings.llm.default:

        def _generated(gen) -> dict[str, Any]:
            return dict(
                status=gen.status,
                sql=gen.sql,
                explanation=gen.explanation,
                assumptions=gen.assumptions,
                warnings=gen.warnings,
                clarification_question=gen.clarification_question,
                generation_id=gen.id,
            )

        @server.tool(annotations=read_only)
        def generate_sql(datasource: str, question: str) -> GeneratedSQL:
            """Let sqlsentry write a commented SQL query for a plain-English question, using the
            datasource's glossary and examples. Returns SQL plus an explanation, or a clarifying
            question if the request is ambiguous. Does not run the query."""
            try:
                gen = sentry.generate(datasource, question, consumer="mcp")
            except SQLSentryError as e:
                raise _fail(e) from e
            return GeneratedSQL(**_generated(gen))

        @server.tool(annotations=read_only)
        def ask(datasource: str, question: str, max_rows: int = DEFAULT_MAX_ROWS) -> Answer:
            """Answer a plain-English question end to end: sqlsentry writes the SQL, checks it, runs it
            read-only and returns the SQL, an explanation and the rows."""
            try:
                a = sentry.ask(datasource, question, consumer="mcp", max_rows=_rows_cap(max_rows))
            except SQLSentryError as e:
                raise _fail(e) from e
            gen, res = a.generation, a.result
            result = None
            if res is not None:
                result = QueryResult(
                    datasource=datasource,
                    sql=gen.sql or "",
                    columns=res.columns,
                    rows=res.rows,
                    row_count=res.row_count,
                    truncated=res.truncated,
                    warnings=gen.warnings,
                )
            return Answer(**_generated(gen), result=result)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sqlsentry-mcp", description="sqlsentry MCP server (stdio): safe database access for AI agents."
    )
    parser.add_argument("-c", "--config", help="config file (absolute path recommended)")
    parser.add_argument("--version", action="version", version=f"sqlsentry {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging (to stderr)")
    args = parser.parse_args(argv)

    # stdout carries the MCP protocol, so all logging must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("The MCP server needs: pip install 'sqlsentry[mcp]'", file=sys.stderr)
        return 1
    # Keys (e.g. GROQ_API_KEY) come from the MCP client's env settings, or from a .env file next to
    # the config or in the working directory. Variables already set always win.
    from pathlib import Path

    from .cli import _load_dotenv

    if args.config:
        _load_dotenv(Path(args.config).resolve().parent / ".env")
    _load_dotenv(Path(".env"))
    try:
        sentry = SQLSentry.from_config(args.config)
    except SQLSentryError as e:
        print(f"error [{e.code}]: {e.message}", file=sys.stderr)
        return 1
    with sentry:
        build_server(sentry).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())

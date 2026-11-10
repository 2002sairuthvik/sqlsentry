"""The browser playground: /playground (enable with ``server.playground.enabled`` or
``sqlsentry serve --playground``).

It needs no API key, so it is deliberately narrow:

* only the datasources listed in ``server.playground.datasources`` are reachable;
* a schema pasted by a visitor becomes a temporary, schema-only datasource: never executed,
  size-limited, kept in memory for the most recent few schemas only;
* every visitor IP is rate-limited (stricter for model calls than for guard checks);
* nothing a visitor sends is written to the history store by the playground config we ship.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from functools import cache
from importlib.resources import files
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..config import DataSourceConfig
from ..engine import SQLSentry
from ..errors import BadRequest, ConfigError, DataSourceNotFound
from ..policy import Policy, TablePolicy
from ..prompts import render_schema
from ..types import ValidationResult
from .ratelimit import RateLimiter

Dialect = Literal[
    "postgres", "mysql", "sqlite", "tsql", "snowflake", "bigquery", "duckdb",
    "oracle", "redshift", "trino", "databricks", "clickhouse",
]  # fmt: skip

MAX_CUSTOM_SCHEMAS = 32

router = APIRouter(prefix="/playground", include_in_schema=False)


class Target(BaseModel):
    datasource: str | None = Field(None, description="A playground datasource from the config")
    schema_ddl: str | None = Field(None, description="Or: CREATE TABLE statements pasted by the visitor")
    dialect: Dialect = "postgres"


class AskRequest(Target):
    question: str = Field(min_length=1, max_length=500)
    execute: bool = True


class ValidateRequest(Target):
    sql: str = Field(min_length=1, max_length=10_000)


class SchemaRequest(BaseModel):
    schema_ddl: str
    dialect: Dialect = "postgres"


class CustomSchemas:
    """Temporary schema-only datasources for pasted DDL, most recent first, bounded."""

    def __init__(self, sentry: SQLSentry, limit: int = MAX_CUSTOM_SCHEMAS):
        self.sentry, self.limit = sentry, limit
        self._names: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def register(self, ddl: str, dialect: str, max_rows: int) -> str:
        name = "playground-" + hashlib.sha256(f"{dialect}\n{ddl}".encode()).hexdigest()[:16]
        with self._lock:
            if name in self._names:
                self._names.move_to_end(name)
                return name
            cfg = DataSourceConfig(
                schema_ddl=ddl,
                dialect=dialect,
                description="Schema pasted into the playground",
                sample_values=0,
                policy=Policy(tables=TablePolicy(include=["*"]), max_rows=max_rows, allow_execute=False),
            )
            self.sentry.add_datasource(name, cfg, temporary=True)
            self._names[name] = None
            while len(self._names) > self.limit:
                old, _ = self._names.popitem(last=False)
                self.sentry.remove_datasource(old)
        try:
            self.sentry.schema(name)  # parse now so bad DDL is reported to the visitor right away
        except ConfigError as e:
            self.forget(name)
            raise BadRequest(e.message) from e
        return name

    def forget(self, name: str) -> None:
        with self._lock:
            self._names.pop(name, None)
        self.sentry.remove_datasource(name)


def _state(request: Request):
    return request.app.state.sentry, request.app.state.sentry.settings.server.playground


def _visitor(request: Request) -> str:
    _, pg = _state(request)
    if pg.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _allowed(sentry: SQLSentry, pg) -> list[str]:
    names = sentry.datasources()
    return [n for n in names if n in pg.datasources] if pg.datasources else names


def _resolve(request: Request, target: Target) -> str:
    sentry, pg = _state(request)
    if target.schema_ddl is not None:
        if not pg.allow_custom_schema:
            raise BadRequest("Pasting a schema is disabled in this playground.")
        ddl = target.schema_ddl.strip()
        if not ddl:
            raise BadRequest("The pasted schema is empty.")
        if len(ddl) > pg.custom_schema_max_chars:
            raise BadRequest(f"The pasted schema is longer than {pg.custom_schema_max_chars} characters.")
        return request.app.state.playground_schemas.register(ddl, target.dialect, pg.max_rows)
    if target.datasource and target.datasource in _allowed(sentry, pg):
        return target.datasource
    raise DataSourceNotFound(f"Unknown datasource '{target.datasource}'")


@cache
def _page() -> str:
    return files("sqlsentry.server").joinpath("static/playground.html").read_text(encoding="utf-8")


@router.get("", response_class=HTMLResponse)
def page() -> HTMLResponse:
    return HTMLResponse(_page(), headers={"Cache-Control": "no-cache"})


@router.get("/api/info")
def info(request: Request) -> dict:
    sentry, pg = _state(request)
    out = []
    for name in _allowed(sentry, pg):
        ds = sentry.datasource(name)
        questions = pg.sample_questions.get(name) or [e.question for e in ds.examples]
        out.append(
            {
                "name": name,
                "description": ds.description,
                "dialect": ds.resolved_dialect,
                "can_execute": ds.can_connect and ds.policy.allow_execute,
                "schema_text": render_schema(sentry.schema(name)),
                "hidden_columns": ds.policy.columns.hidden,
                "sample_questions": questions,
            }
        )
    default = sentry.settings.llm.default
    model = sentry.settings.llm.providers[default].model if default else None
    return {
        "version": __version__,
        "model": model,
        "datasources": out,
        "allow_custom_schema": pg.allow_custom_schema,
        "custom_schema_max_chars": pg.custom_schema_max_chars,
        "max_rows": pg.max_rows,
    }


@router.post("/api/schema")
def preview_schema(body: SchemaRequest, request: Request) -> dict:
    """Show the visitor how their pasted DDL was understood."""
    request.app.state.playground_light_limiter.check(_visitor(request))
    sentry, _ = _state(request)
    name = _resolve(request, Target(schema_ddl=body.schema_ddl, dialect=body.dialect))
    catalog = sentry.schema(name)
    return {"tables": catalog.table_names(), "schema_text": render_schema(catalog)}


@router.post("/api/validate", response_model=ValidationResult)
def validate(body: ValidateRequest, request: Request) -> ValidationResult:
    """The guard, with no model involved: free to call, so a looser rate limit."""
    request.app.state.playground_light_limiter.check(_visitor(request))
    sentry, _ = _state(request)
    return sentry.validate(_resolve(request, body), body.sql)


@router.post("/api/ask")
def ask(body: AskRequest, request: Request) -> dict:
    request.app.state.playground_limiter.check(_visitor(request))
    sentry, pg = _state(request)
    name = _resolve(request, body)
    gen = sentry.generate(name, body.question, consumer="playground")
    ds = sentry.datasource(name)
    result, note = None, None
    if gen.status == "ok" and body.execute:
        if ds.can_connect and ds.policy.allow_execute:
            res = sentry.execute(gen, max_rows=pg.max_rows)
            result = {
                "columns": res.columns,
                "rows": res.rows,
                "row_count": res.row_count,
                "truncated": res.truncated,
            }
        else:
            note = "This schema has no database behind it, so the SQL was generated and checked but not run."
    return {
        "status": gen.status,
        "sql": gen.sql,
        "explanation": gen.explanation,
        "assumptions": gen.assumptions,
        "warnings": gen.warnings,
        "clarification_question": gen.clarification_question,
        "dialect": gen.dialect,
        "model": gen.model,
        "attempts": len(gen.attempts),
        "latency_ms": gen.latency_ms,
        "result": result,
        "note": note,
    }


def install(app, sentry: SQLSentry) -> None:
    pg = sentry.settings.server.playground
    app.state.playground_schemas = CustomSchemas(sentry)
    app.state.playground_limiter = RateLimiter(pg.requests_per_minute)
    app.state.playground_light_limiter = RateLimiter(pg.requests_per_minute * 6)
    app.include_router(router)

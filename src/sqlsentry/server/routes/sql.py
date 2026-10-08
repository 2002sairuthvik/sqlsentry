from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...engine import SQLSentry
from ...errors import GenerationFailed
from ...types import ExecutionResult, ValidationResult
from ..auth import Authenticator, Principal
from ..deps import audit, principal, sentry
from ..schemas import (
    ExecuteRequest,
    GenerateRequest,
    GenerateResponse,
    QueryRequest,
    QueryResponse,
    ValidateRequest,
)

router = APIRouter(prefix="/v1/sql", tags=["sql"])


def _owner(p: Principal) -> str | None:
    return None if p.consumer is None else p.name


@router.post("/generate", response_model=GenerateResponse, summary="Question -> SQL (SQL only)")
def generate(
    body: GenerateRequest,
    request: Request,
    p: Principal = Depends(principal),
    s: SQLSentry = Depends(sentry),
) -> GenerateResponse:
    Authenticator.require(p, body.datasource, "generate")
    try:
        gen = s.generate(body.datasource, body.question, consumer=p.name, provider=body.provider)
    except GenerationFailed as e:
        audit(request, "generate", datasource=body.datasource, question=body.question[:500], status="failed",
              generation_id=e.details.get("generation_id"))  # fmt: skip
        raise
    audit(request, "generate", datasource=body.datasource, question=body.question[:500], status=gen.status,
          generation_id=gen.id, sql=gen.sql, model=gen.model, latency_ms=gen.latency_ms,
          tokens=gen.usage.model_dump())  # fmt: skip
    return GenerateResponse.from_generation(gen)


@router.post("/query", response_model=QueryResponse, summary="Question -> SQL + rows (batteries included)")
def query(
    body: QueryRequest,
    request: Request,
    p: Principal = Depends(principal),
    s: SQLSentry = Depends(sentry),
) -> QueryResponse:
    Authenticator.require(p, body.datasource, "generate")
    Authenticator.require(p, body.datasource, "execute")
    try:
        answer = s.ask(
            body.datasource, body.question, consumer=p.name, provider=body.provider, max_rows=body.max_rows
        )
    except GenerationFailed as e:
        audit(request, "query", datasource=body.datasource, question=body.question[:500], status="failed",
              generation_id=e.details.get("generation_id"))  # fmt: skip
        raise
    gen, result = answer.generation, answer.result
    audit(request, "query", datasource=body.datasource, question=body.question[:500], status=gen.status,
          generation_id=gen.id, sql=gen.sql, model=gen.model, latency_ms=gen.latency_ms,
          tokens=gen.usage.model_dump(), row_count=result.row_count if result else None)  # fmt: skip
    return QueryResponse(**GenerateResponse.from_generation(gen).model_dump(), result=result)


@router.post("/execute", response_model=ExecutionResult, summary="Run a generation or your own (edited) SQL")
def execute(
    body: ExecuteRequest,
    request: Request,
    p: Principal = Depends(principal),
    s: SQLSentry = Depends(sentry),
) -> ExecutionResult:
    if body.generation_id is not None:
        gen = s.get_generation(body.generation_id, consumer=_owner(p))
        Authenticator.require(p, gen.datasource, "execute")
        result = s.execute(gen, consumer=_owner(p), max_rows=body.max_rows)
        audit(request, "execute", datasource=gen.datasource, generation_id=gen.id, row_count=result.row_count,
              truncated=result.truncated, duration_ms=result.duration_ms)  # fmt: skip
        return result

    Authenticator.require(p, body.datasource, "execute")
    result = s.execute_sql(body.datasource, body.sql, max_rows=body.max_rows, consumer=_owner(p))
    audit(request, "execute_sql", datasource=body.datasource, sql=body.sql[:5000], row_count=result.row_count,
          truncated=result.truncated, duration_ms=result.duration_ms)  # fmt: skip
    return result


@router.post("/validate", response_model=ValidationResult, summary="Check SQL against the guard and policy")
def validate(
    body: ValidateRequest,
    request: Request,
    p: Principal = Depends(principal),
    s: SQLSentry = Depends(sentry),
) -> ValidationResult:
    Authenticator.require(p, body.datasource, "validate")
    result = s.validate(body.datasource, body.sql)
    audit(request, "validate", datasource=body.datasource, valid=result.valid)
    return result

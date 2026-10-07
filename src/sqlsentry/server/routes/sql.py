from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...engine import SQLSentry
from ...errors import GenerationFailed
from ...types import ExecutionResult, ValidationResult
from ..auth import Authenticator, Principal
from ..deps import audit, principal, sentry
from ..schemas import ExecuteRequest, GenerateRequest, GenerateResponse, ValidateRequest

router = APIRouter(prefix="/v1/sql", tags=["sql"])


@router.post("/generate", response_model=GenerateResponse)
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


@router.post("/execute", response_model=ExecutionResult)
def execute(
    body: ExecuteRequest,
    request: Request,
    p: Principal = Depends(principal),
    s: SQLSentry = Depends(sentry),
) -> ExecutionResult:
    owner = None if p.consumer is None else p.name
    gen = s.get_generation(body.generation_id, consumer=owner)
    Authenticator.require(p, gen.datasource, "execute")
    result = s.execute(gen, consumer=owner, max_rows=body.max_rows)
    audit(request, "execute", datasource=gen.datasource, generation_id=gen.id, row_count=result.row_count,
          truncated=result.truncated, duration_ms=result.duration_ms)  # fmt: skip
    return result


@router.post("/validate", response_model=ValidationResult)
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

"""Request/response bodies of the REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..types import Generation


class GenerateRequest(BaseModel):
    datasource: str
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = Field(None, description="LLM provider override (must be allowed by policy).")


class GenerateResponse(BaseModel):
    generation_id: str
    status: Literal["ok", "needs_clarification", "failed"]
    sql: str | None
    explanation: str | None
    tables_used: list[str]
    assumptions: list[str]
    warnings: list[str]
    clarification_question: str | None
    dialect: str
    model: str
    attempts: int
    latency_ms: int

    @classmethod
    def from_generation(cls, g: Generation) -> GenerateResponse:
        return cls(
            generation_id=g.id,
            status=g.status,
            sql=g.sql,
            explanation=g.explanation,
            tables_used=g.tables_used,
            assumptions=g.assumptions,
            warnings=g.warnings,
            clarification_question=g.clarification_question,
            dialect=g.dialect,
            model=g.model,
            attempts=len(g.attempts),
            latency_ms=g.latency_ms,
        )


class ExecuteRequest(BaseModel):
    generation_id: str
    max_rows: int | None = Field(None, ge=1)


class ValidateRequest(BaseModel):
    datasource: str
    sql: str = Field(min_length=1, max_length=50_000)


class FeedbackRequest(BaseModel):
    rating: Literal["correct", "incorrect"]
    corrected_sql: str | None = Field(None, max_length=50_000)
    comment: str | None = Field(None, max_length=2000)


class DatasourceInfo(BaseModel):
    id: str
    description: str
    dialect: str
    scopes: list[str]

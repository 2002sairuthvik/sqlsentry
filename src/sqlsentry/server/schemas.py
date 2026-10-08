"""Request/response bodies of the REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..types import ExecutionResult, Generation


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
    """Run either a previous generation (``generation_id``) or SQL you wrote or edited
    (``datasource`` + ``sql``). Edited SQL goes through the same guard and policy."""

    generation_id: str | None = None
    datasource: str | None = None
    sql: str | None = Field(None, min_length=1, max_length=50_000)
    max_rows: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _one_form(self) -> ExecuteRequest:
        by_id = self.generation_id is not None
        by_sql = self.datasource is not None or self.sql is not None
        if by_id == by_sql or (by_sql and not (self.datasource and self.sql)):
            raise ValueError("send either 'generation_id', or both 'datasource' and 'sql'")
        return self


class QueryRequest(BaseModel):
    datasource: str
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = Field(None, description="LLM provider override (must be allowed by policy).")
    max_rows: int | None = Field(None, ge=1)


class ValidateRequest(BaseModel):
    datasource: str
    sql: str = Field(min_length=1, max_length=50_000)


class FeedbackRequest(BaseModel):
    rating: Literal["correct", "incorrect"]
    corrected_sql: str | None = Field(None, max_length=50_000)
    comment: str | None = Field(None, max_length=2000)


class QueryResponse(GenerateResponse):
    """A generation plus its rows. ``result`` is null when the model asked for clarification."""

    result: ExecutionResult | None


class DatasourceInfo(BaseModel):
    id: str
    description: str
    dialect: str
    mode: Literal["connected", "schema_only"]
    can_execute: bool
    scopes: list[str]

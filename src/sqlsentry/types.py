"""Result and record types shared by the library, the API and the store."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class SQLCandidate(BaseModel):
    """What the LLM must return (as JSON)."""

    sql: str = ""
    explanation: str = ""
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None


# Hand-written so it is valid for strict structured-output modes (all keys required,
# no additional properties) and readable when embedded in prompts.
CANDIDATE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "One read-only SELECT with comments; empty if clarifying"},
        "explanation": {"type": "string", "description": "Plain-English explanation of what the SQL does"},
        "tables_used": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": [
        "sql",
        "explanation",
        "tables_used",
        "assumptions",
        "needs_clarification",
        "clarification_question",
    ],
    "additionalProperties": False,
}


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


class Attempt(BaseModel):
    number: int
    stage: Literal["parse", "guard", "verify", "ok", "clarification"]
    sql: str | None = None
    error: str | None = None


class Generation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("gen"))
    datasource: str
    question: str
    consumer: str | None = None
    status: Literal["ok", "needs_clarification", "failed"]
    sql: str | None = None
    explanation: str | None = None
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    dialect: str
    provider: str
    model: str
    attempts: list[Attempt] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    policy_fingerprint: str = ""
    schema_fingerprint: str = ""
    created_at: datetime = Field(default_factory=_now)


class ExecutionResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("exe"))
    generation_id: str | None = None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int


class ExecutionRecord(BaseModel):
    """What is stored about an execution. Never the rows."""

    id: str
    generation_id: str | None
    consumer: str | None
    row_count: int
    truncated: bool
    duration_ms: int
    created_at: datetime = Field(default_factory=_now)


class ValidationIssue(BaseModel):
    code: str
    message: str


class ValidationResult(BaseModel):
    valid: bool
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[ValidationIssue] = Field(default_factory=list)


class Feedback(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fb"))
    generation_id: str
    rating: Literal["correct", "incorrect"]
    corrected_sql: str | None = None
    comment: str | None = None
    consumer: str | None = None
    created_at: datetime = Field(default_factory=_now)

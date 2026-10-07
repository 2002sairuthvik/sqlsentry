"""Layer 2 policy: the editable rules a datasource owner controls.

Layer 1 (the locked read-only baseline) lives in ``sqlsentry.guard.baseline`` and has no
configuration. This module only narrows what Layer 1 already allows.

Matching is case-insensitive and supports shell-style wildcards (``audit_*``, ``*.email``).
Tables are **default-deny**: nothing is visible until ``tables.include`` lists it.
"""

from __future__ import annotations

import hashlib
from fnmatch import fnmatchcase
from typing import Literal

from pydantic import BaseModel, Field

Scope = Literal["generate", "execute", "validate", "schema"]
ALL_SCOPES: tuple[Scope, ...] = ("generate", "execute", "validate", "schema")


def _match(name: str, patterns: list[str]) -> bool:
    name = name.lower()
    return any(fnmatchcase(name, p.lower()) for p in patterns)


class TablePolicy(BaseModel):
    include: list[str] = Field(default_factory=list, description="Visible tables. Default: none.")
    exclude: list[str] = Field(default_factory=list, description="Hidden even if included.")


class ColumnPolicy(BaseModel):
    hidden: list[str] = Field(
        default_factory=list,
        description="'table.column' patterns, e.g. 'customers.email', '*.ssn'. Never shown or queryable.",
    )


class Policy(BaseModel):
    tables: TablePolicy = Field(default_factory=TablePolicy)
    columns: ColumnPolicy = Field(default_factory=ColumnPolicy)
    max_rows: int = Field(1000, ge=1, le=1_000_000)
    timeout_s: int = Field(30, ge=1, le=3600)
    allow_execute: bool = False
    allowed_providers: list[str] = Field(
        default_factory=lambda: ["*"],
        description="LLM provider names this datasource's schema may be sent to.",
    )

    def table_allowed(self, table: str) -> bool:
        return _match(table, self.tables.include) and not _match(table, self.tables.exclude)

    def column_hidden(self, table: str, column: str) -> bool:
        return _match(f"{table}.{column}", self.columns.hidden)

    def provider_allowed(self, provider: str) -> bool:
        return _match(provider, self.allowed_providers)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]


class Grant(BaseModel):
    datasource: str
    scopes: list[Scope] = Field(default_factory=lambda: ["generate", "validate", "schema"])


class Consumer(BaseModel):
    """A caller of the REST API (a team, a service, a person)."""

    name: str
    key_sha256: list[str] = Field(default_factory=list, description="SHA-256 hex digests of API keys.")
    grants: list[Grant] = Field(default_factory=list)

    def scopes_for(self, datasource: str) -> set[str]:
        scopes: set[str] = set()
        for g in self.grants:
            if g.datasource == "*" or g.datasource == datasource:
                scopes.update(g.scopes)
        return scopes

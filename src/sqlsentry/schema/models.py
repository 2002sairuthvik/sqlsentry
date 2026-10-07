"""Schema catalog: what sqlsentry knows about a datasource.

The catalog is also the "context bundle" format: it serialises to versioned JSON so a
schema can be exported inside a private network and loaded elsewhere.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ..policy import Policy

BUNDLE_VERSION = 1


class Column(BaseModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    comment: str | None = None
    sample_values: list[str] = Field(default_factory=list)


class ForeignKey(BaseModel):
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


class Table(BaseModel):
    name: str
    comment: str | None = None
    columns: list[Column] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)

    def column(self, name: str) -> Column | None:
        lname = name.lower()
        return next((c for c in self.columns if c.name.lower() == lname), None)


class SchemaCatalog(BaseModel):
    bundle_version: int = BUNDLE_VERSION
    datasource: str
    dialect: str
    db_schema: str | None = None
    tables: list[Table] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def table(self, name: str) -> Table | None:
        lname = name.lower()
        return next((t for t in self.tables if t.name.lower() == lname), None)

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def filtered(self, policy: Policy) -> SchemaCatalog:
        """Apply Layer 2: drop non-allowed tables, hidden columns, and dangling foreign keys."""
        tables: list[Table] = []
        for t in self.tables:
            if not policy.table_allowed(t.name):
                continue
            cols = [c for c in t.columns if not policy.column_hidden(t.name, c.name)]
            tables.append(t.model_copy(update={"columns": cols}))
        visible = {t.name.lower(): {c.name.lower() for c in t.columns} for t in tables}
        for t in tables:
            t.foreign_keys = [
                fk
                for fk in t.foreign_keys
                if fk.ref_table.lower() in visible
                and all(c.lower() in visible[t.name.lower()] for c in fk.columns)
                and all(c.lower() in visible[fk.ref_table.lower()] for c in fk.ref_columns)
            ]
        return self.model_copy(update={"tables": tables})

    def subset(self, names: list[str]) -> SchemaCatalog:
        wanted = {n.lower() for n in names}
        return self.model_copy(update={"tables": [t for t in self.tables if t.name.lower() in wanted]})

    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {t.name: {c.name: c.type or "UNKNOWN" for c in t.columns} for t in self.tables}

    def fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"captured_at"})
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

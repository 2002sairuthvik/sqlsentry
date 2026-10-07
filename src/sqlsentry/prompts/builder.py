"""Prompt construction.

The system prompt holds only stable instructions (good for provider-side prompt caching);
everything that varies per question goes in the user message. Only the policy-filtered
schema, glossary, examples and the question are ever sent to a model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

from ..config import FewShotExample
from ..llm.base import Message
from ..schema.models import SchemaCatalog, Table
from ..types import CANDIDATE_JSON_SCHEMA


@dataclass
class Prompt:
    system: str
    messages: list[Message] = field(default_factory=list)


@cache
def _system_template() -> str:
    return files("sqlsentry.prompts").joinpath("system.md").read_text(encoding="utf-8")


def system_prompt(dialect: str) -> str:
    return (
        _system_template()
        .replace("{dialect}", dialect)
        .replace("{json_schema}", json.dumps(CANDIDATE_JSON_SCHEMA, indent=2))
    )


def render_table(t: Table) -> str:
    head = f"TABLE {t.name}" + (f"  -- {t.comment}" if t.comment else "")
    lines = [head]
    for c in t.columns:
        line = f"  {c.name} {c.type}"
        if c.primary_key:
            line += " PRIMARY KEY"
        notes = []
        if c.comment:
            notes.append(c.comment)
        if c.sample_values:
            notes.append("e.g. " + ", ".join(repr(v) for v in c.sample_values))
        if notes:
            line += "  -- " + "; ".join(notes)
        lines.append(line)
    for fk in t.foreign_keys:
        lines.append(
            f"  FOREIGN KEY ({', '.join(fk.columns)}) REFERENCES {fk.ref_table}({', '.join(fk.ref_columns)})"
        )
    return "\n".join(lines)


def render_schema(catalog: SchemaCatalog) -> str:
    return "\n\n".join(render_table(t) for t in catalog.tables)


def build_prompt(
    *,
    question: str,
    catalog: SchemaCatalog,
    dialect: str,
    datasource: str,
    description: str = "",
    glossary: list[str] | None = None,
    examples: list[FewShotExample] | None = None,
    other_tables: list[str] | None = None,
) -> Prompt:
    parts = [f"Database: {datasource} ({dialect})"]
    if description:
        parts.append(f"Description: {description}")
    parts.append("Schema:\n" + render_schema(catalog))
    if other_tables:
        parts.append(
            "Other tables exist but were not shown because they look unrelated: "
            + ", ".join(other_tables)
            + ". If you need one, ask for clarification."
        )
    if glossary:
        parts.append("Glossary:\n" + "\n".join(f"- {g}" for g in glossary))
    if examples:
        shots = "\n\n".join(f"Q: {e.question}\nSQL:\n{e.sql.strip()}" for e in examples)
        parts.append("Verified examples:\n" + shots)
    parts.append(f"Question: {question}")
    return Prompt(system=system_prompt(dialect), messages=[{"role": "user", "content": "\n\n".join(parts)}])


def repair_messages(raw_response: str, error: str) -> list[Message]:
    """Turn a failed attempt into conversation turns asking the model to fix it."""
    return [
        {"role": "assistant", "content": raw_response or "(empty response)"},
        {
            "role": "user",
            "content": (
                f"That response was rejected:\n{error}\n\n"
                "Fix the problem and reply with a corrected JSON object that follows every rule."
            ),
        },
    ]

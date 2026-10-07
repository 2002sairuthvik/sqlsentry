"""Schema linking: pick the tables (and examples) relevant to a question.

Large schemas don't fit in a prompt, and irrelevant tables make models worse. v0.1 uses
transparent keyword scoring plus foreign-key expansion; an embedding-based linker can
replace it later behind the same functions.
"""

from __future__ import annotations

import re

from ..config import FewShotExample
from .models import SchemaCatalog, Table

_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "by", "with", "and", "or", "is", "are", "was",
    "what", "which", "who", "how", "many", "much", "show", "me", "list", "give", "get", "find", "all",
    "per", "each", "from", "that", "this", "their", "there", "top", "most", "least", "last", "first",
    "do", "does", "did", "have", "has", "had", "be", "it", "its", "as", "at", "than", "more", "less",
}  # fmt: skip

_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> set[str]:
    """Lower-case word stems; snake_case and camelCase are split; plurals folded."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower().replace("_", " ")
    out: set[str] = set()
    for w in _WORD.findall(text):
        if w in _STOPWORDS or len(w) < 2:
            continue
        out.add(_stem(w))
    return out


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _score(q: set[str], t: Table) -> float:
    score = 3.0 * len(q & tokens(t.name))
    if t.comment:
        score += len(q & tokens(t.comment))
    for c in t.columns:
        score += 1.0 * len(q & tokens(c.name))
        if c.comment:
            score += 0.5 * len(q & tokens(c.comment))
        if c.sample_values:
            score += 2.0 * len(q & tokens(" ".join(c.sample_values)))
    return score


def link_tables(question: str, catalog: SchemaCatalog, max_tables: int) -> SchemaCatalog:
    if len(catalog.tables) <= max_tables:
        return catalog
    q = tokens(question)
    ranked = sorted(catalog.tables, key=lambda t: _score(q, t), reverse=True)
    chosen = [t.name for t in ranked if _score(q, t) > 0][:max_tables]
    if not chosen:
        return catalog.subset([t.name for t in ranked[:max_tables]])

    # Add join partners so the model can actually connect the chosen tables.
    neighbours: dict[str, set[str]] = {t.name.lower(): set() for t in catalog.tables}
    for t in catalog.tables:
        for fk in t.foreign_keys:
            neighbours[t.name.lower()].add(fk.ref_table.lower())
            neighbours.setdefault(fk.ref_table.lower(), set()).add(t.name.lower())
    result = {n.lower() for n in chosen}
    for name in list(result):
        for n in sorted(neighbours.get(name, ())):
            if len(result) >= max_tables:
                break
            result.add(n)
    return catalog.subset(sorted(result))


def select_examples(question: str, examples: list[FewShotExample], k: int) -> list[FewShotExample]:
    if k <= 0 or not examples:
        return []
    q = tokens(question)

    def sim(e: FewShotExample) -> float:
        et = tokens(e.question)
        return len(q & et) / (len(q | et) or 1)

    return sorted(examples, key=sim, reverse=True)[:k]

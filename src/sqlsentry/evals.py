"""Accuracy evaluation on a golden question -> SQL dataset.

A case passes when the generated SQL returns the golden SQL's result set (row order and
column order ignored, extra columns allowed, numbers compared after rounding to 2 dp).
Comparing results instead of SQL text is what matters: many different queries are correct.

Dataset format (YAML):

    datasource: store
    cases:
      - id: cancelled-orders
        question: How many orders were cancelled?
        sql: SELECT COUNT(*) FROM orders WHERE status = 'cancelled'
"""

from __future__ import annotations

import time
from collections import Counter
from itertools import islice, product
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .engine import SQLSentry
from .errors import ConfigError, SQLSentryError
from .types import Usage

EVAL_ROW_CAP = 5000


class EvalCase(BaseModel):
    id: str | None = None
    question: str
    sql: str


class EvalDataset(BaseModel):
    datasource: str
    cases: list[EvalCase] = Field(default_factory=list)


class CaseResult(BaseModel):
    id: str
    question: str
    status: Literal["pass", "fail", "clarification", "error"]
    generated_sql: str | None = None
    error: str | None = None
    attempts: int = 0
    latency_ms: int = 0


class EvalReport(BaseModel):
    dataset: str
    datasource: str
    provider: str
    model: str
    total: int
    passed: int
    accuracy: float
    avg_latency_ms: int
    usage: Usage
    results: list[CaseResult]


def load_dataset(path: str | Path) -> EvalDataset:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Eval dataset not found: {p}")
    return EvalDataset.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


def _norm(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    return str(v).strip()


_MAX_COLUMN_MAPPINGS = 500


def results_match(expected: list[list[Any]], actual: list[list[Any]]) -> bool:
    """True if ``actual`` contains ``expected``'s data: same rows (any order), and every
    expected column appears among the actual columns (any order, extra columns allowed)."""
    exp = [tuple(_norm(v) for v in r) for r in expected]
    act = [tuple(_norm(v) for v in r) for r in actual]
    if len(exp) != len(act):
        return False
    if Counter(exp) == Counter(act):
        return True
    if not exp:
        return True
    n_exp, n_act = len(exp[0]), len(act[0])
    if n_act < n_exp:
        return False

    # Candidate actual columns for each expected column: same multiset of values.
    def column(rows, i):
        return Counter(r[i] for r in rows)

    candidates = [[j for j in range(n_act) if column(act, j) == column(exp, i)] for i in range(n_exp)]
    if any(not c for c in candidates):
        return False
    target = Counter(exp)
    for mapping in islice(product(*candidates), _MAX_COLUMN_MAPPINGS):
        if len(set(mapping)) == n_exp and Counter(tuple(r[j] for j in mapping) for r in act) == target:
            return True
    return False


def run_eval(sentry: SQLSentry, path: str | Path, *, provider: str | None = None) -> EvalReport:
    ds = load_dataset(path)
    llm = sentry.provider(ds.datasource, provider)
    usage = Usage()
    results: list[CaseResult] = []

    for n, case in enumerate(ds.cases, 1):
        cid = case.id or str(n)
        started = time.perf_counter()
        try:
            gold = sentry.execute_sql(ds.datasource, case.sql, max_rows=EVAL_ROW_CAP)
            gen = sentry.generate(ds.datasource, case.question, provider=provider, consumer="eval")
            usage.add(gen.usage)
            if gen.status != "ok":
                results.append(
                    CaseResult(
                        id=cid,
                        question=case.question,
                        status="clarification",
                        error=gen.clarification_question,
                        attempts=len(gen.attempts),
                    )
                )
                continue
            pred = sentry.execute(gen, max_rows=EVAL_ROW_CAP)
            ok = results_match(gold.rows, pred.rows)
            results.append(
                CaseResult(
                    id=cid,
                    question=case.question,
                    status="pass" if ok else "fail",
                    generated_sql=gen.sql,
                    attempts=len(gen.attempts),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            )
        except SQLSentryError as e:
            results.append(
                CaseResult(id=cid, question=case.question, status="error", error=f"{e.code}: {e.message}")
            )

    passed = sum(r.status == "pass" for r in results)
    timed = [r.latency_ms for r in results if r.latency_ms]
    return EvalReport(
        dataset=str(path),
        datasource=ds.datasource,
        provider=llm.name,
        model=llm.model,
        total=len(results),
        passed=passed,
        accuracy=passed / len(results) if results else 0.0,
        avg_latency_ms=int(sum(timed) / len(timed)) if timed else 0,
        usage=usage,
        results=results,
    )


def format_report(r: EvalReport) -> str:
    lines = [f"{r.dataset}  ({r.datasource}, {r.provider}/{r.model})", ""]
    for c in r.results:
        mark = {"pass": "PASS", "fail": "FAIL", "clarification": "ASK ", "error": "ERR "}[c.status]
        lines.append(f"  {mark}  {c.id:<28} {c.question[:60]}")
        if c.status in ("error", "clarification") and c.error:
            lines.append(f"        {c.error[:200]}")
    lines += [
        "",
        f"accuracy {r.passed}/{r.total} = {r.accuracy:.0%}   avg latency {r.avg_latency_ms} ms   "
        f"tokens in {r.usage.input_tokens} / out {r.usage.output_tokens}",
    ]
    return "\n".join(lines)

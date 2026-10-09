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
from collections.abc import Callable
from itertools import islice, product
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .engine import SQLSentry
from .errors import ConfigError, LLMError, SQLSentryError
from .llm.base import is_daily_limit
from .types import Usage

EVAL_ROW_CAP = 5000
MAX_RATE_LIMIT_WAIT_S = 120.0


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
    status: Literal["pass", "fail", "clarification", "error", "rate_limited"]
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
    rate_limited: int = 0
    answered_accuracy: float = Field(0.0, description="passed / cases that weren't rate-limited")
    rate_limit_waits: int = 0
    stopped_early: str | None = Field(None, description="Why the run stopped before the end")
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


def run_eval(
    sentry: SQLSentry,
    path: str | Path,
    *,
    provider: str | None = None,
    rate_limit_retries: int = 3,
    rate_limit_wait_s: float = 30.0,
    pace_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> EvalReport:
    """Run every case. When the provider rate-limits (HTTP 429), wait as long as it asks
    (or ``rate_limit_wait_s``) and retry the case, up to ``rate_limit_retries`` times; cases
    that stay rate-limited are reported as ``rate_limited``, separate from wrong answers.
    ``pace_s`` spaces cases out to stay under per-minute limits in the first place."""
    ds = load_dataset(path)
    llm = sentry.provider(ds.datasource, provider)
    usage = Usage()
    results: list[CaseResult] = []
    waits = 0
    stopped: str | None = None

    for n, case in enumerate(ds.cases, 1):
        cid = case.id or str(n)
        if stopped:
            results.append(CaseResult(id=cid, question=case.question, status="rate_limited", error="not run"))
            continue
        if pace_s and n > 1:
            sleep(pace_s)
        try:
            gold = sentry.execute_sql(ds.datasource, case.sql, max_rows=EVAL_ROW_CAP)
        except SQLSentryError as e:
            results.append(
                CaseResult(
                    id=cid,
                    question=case.question,
                    status="error",
                    error=f"golden SQL failed: {e.code}: {e.message}",
                )
            )
            continue

        for retry in range(rate_limit_retries + 1):
            try:
                gen = sentry.generate(ds.datasource, case.question, provider=provider, consumer="eval")
            except LLMError as e:
                if not _is_rate_limit(e):
                    results.append(_error_result(cid, case.question, e))
                    break
                if _is_daily_limit(e):
                    # Waiting minutes can't beat a daily cap: stop instead of grinding through retries.
                    stopped = f"daily quota exhausted: {e.message}"
                    results.append(
                        CaseResult(id=cid, question=case.question, status="rate_limited", error=stopped)
                    )
                    break
                if retry == rate_limit_retries:
                    results.append(
                        CaseResult(
                            id=cid,
                            question=case.question,
                            status="rate_limited",
                            error=f"still rate limited after {retry} retries: {e.message}",
                        )
                    )
                    break
                waits += 1
                sleep(_rate_limit_wait(e, rate_limit_wait_s))
                continue
            except SQLSentryError as e:
                results.append(_error_result(cid, case.question, e))
                break

            usage.add(gen.usage)
            if gen.status != "ok":
                results.append(
                    CaseResult(
                        id=cid,
                        question=case.question,
                        status="clarification",
                        error=gen.clarification_question,
                        attempts=len(gen.attempts),
                        latency_ms=gen.latency_ms,
                    )
                )
                break
            try:
                pred = sentry.execute(gen, max_rows=EVAL_ROW_CAP)
            except SQLSentryError as e:
                results.append(_error_result(cid, case.question, e, gen.sql))
                break
            results.append(
                CaseResult(
                    id=cid,
                    question=case.question,
                    status="pass" if results_match(gold.rows, pred.rows) else "fail",
                    generated_sql=gen.sql,
                    attempts=len(gen.attempts),
                    latency_ms=gen.latency_ms,
                )
            )
            break

    passed = sum(r.status == "pass" for r in results)
    limited = sum(r.status == "rate_limited" for r in results)
    answered = len(results) - limited
    timed = [r.latency_ms for r in results if r.latency_ms]
    return EvalReport(
        dataset=str(path),
        datasource=ds.datasource,
        provider=llm.name,
        model=llm.model,
        total=len(results),
        passed=passed,
        accuracy=passed / len(results) if results else 0.0,
        rate_limited=limited,
        answered_accuracy=passed / answered if answered else 0.0,
        rate_limit_waits=waits,
        stopped_early=stopped,
        avg_latency_ms=int(sum(timed) / len(timed)) if timed else 0,
        usage=usage,
        results=results,
    )


def _is_rate_limit(e: LLMError) -> bool:
    return e.details.get("status") == 429


def _is_daily_limit(e: LLMError) -> bool:
    return is_daily_limit(e.message)


def _rate_limit_wait(e: LLMError, default_s: float) -> float:
    """Wait what the provider asked for (plus a small margin), within sane bounds."""
    hint = e.details.get("retry_after_s")
    wait = (hint + 1.0) if isinstance(hint, (int, float)) else default_s
    return min(max(wait, 1.0), MAX_RATE_LIMIT_WAIT_S)


def _error_result(cid: str, question: str, e: SQLSentryError, sql: str | None = None) -> CaseResult:
    return CaseResult(
        id=cid, question=question, status="error", generated_sql=sql, error=f"{e.code}: {e.message}"
    )


def format_report(r: EvalReport) -> str:
    lines = [f"{r.dataset}  ({r.datasource}, {r.provider}/{r.model})", ""]
    for c in r.results:
        mark = {
            "pass": "PASS",
            "fail": "FAIL",
            "clarification": "ASK ",
            "error": "ERR ",
            "rate_limited": "RATE",
        }[c.status]
        lines.append(f"  {mark}  {c.id:<28} {c.question[:60]}")
        if c.status in ("error", "clarification", "rate_limited") and c.error:
            lines.append(f"        {c.error[:200]}")
    lines += ["", f"accuracy {r.passed}/{r.total} = {r.accuracy:.0%}"]
    if r.rate_limited:
        answered = r.total - r.rate_limited
        if answered:
            lines[-1] += f"   ({r.passed}/{answered} = {r.answered_accuracy:.0%} of the questions answered; "
        else:
            lines[-1] += "   (no questions were answered; "
        lines[-1] += f"{r.rate_limited} rate-limited)"
    if r.stopped_early:
        not_run = sum(c.error == "not run" for c in r.results)
        lines.append(
            f"STOPPED EARLY: the provider's daily quota is used up "
            f"({not_run} question{'' if not_run == 1 else 's'} not run). "
            "Run again later, or use --provider with another model."
        )
    lines.append(
        f"avg generation latency {r.avg_latency_ms} ms   tokens in {r.usage.input_tokens} / "
        f"out {r.usage.output_tokens}   rate-limit waits {r.rate_limit_waits}"
    )
    return "\n".join(lines)

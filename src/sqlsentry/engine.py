"""The sqlsentry engine: the public library API.

    from sqlsentry import SQLSentry
    sentry = SQLSentry.from_config("sqlsentry.yaml")
    gen = sentry.generate("store", "top 5 customers by revenue")
    print(gen.sql, gen.explanation)
    result = sentry.execute(gen)          # only if the datasource policy allows it

Flow for ``generate``: policy-filtered schema -> schema linking -> prompt -> LLM ->
parse -> guard (Layer 1 + 2) -> dry-run -> repeat with the error on failure.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from sqlalchemy import Engine, create_engine

from .config import DataSourceConfig, Settings
from .errors import (
    BadRequest,
    ConfigError,
    DataSourceNotFound,
    ExecutionError,
    ExecutionUnavailable,
    GenerationFailed,
    GenerationNotFound,
    InvalidSQLError,
    PermissionDenied,
    SQLSentryError,
    UnsafeSQLError,
)
from .execution import dry_run, run_query, supports_dry_run
from .guard import check_sql
from .llm.base import LLMProvider
from .llm.parsing import ParseError, parse_candidate
from .llm.registry import build_provider
from .prompts import build_prompt, repair_messages
from .schema.cache import TTLCache
from .schema.files import load_schema_file
from .schema.introspect import introspect
from .schema.linker import link_tables, select_examples
from .schema.models import SchemaCatalog
from .store import MemoryStore, SQLStore, Store
from .types import (
    CANDIDATE_JSON_SCHEMA,
    Answer,
    Attempt,
    ExecutionRecord,
    ExecutionResult,
    Feedback,
    Generation,
    Usage,
    ValidationIssue,
    ValidationResult,
)

log = logging.getLogger("sqlsentry")

MAX_QUESTION_CHARS = 2000


def _schema_only(datasource: str) -> ExecutionUnavailable:
    return ExecutionUnavailable(
        f"Datasource '{datasource}' is schema-only: SQL can be generated and validated but not run here. "
        "Add a 'url' to its config to enable execution."
    )


class SQLSentry:
    def __init__(
        self,
        settings: Settings,
        *,
        providers: dict[str, LLMProvider] | None = None,
        store: Store | None = None,
    ):
        self.settings = settings
        self._providers: dict[str, LLMProvider] = dict(providers or {})
        self._engines: dict[str, Engine] = {}
        self._catalogs: TTLCache[SchemaCatalog] = TTLCache(settings.engine.schema_cache_ttl_s)
        self._lock = threading.Lock()
        if store is not None:
            self.store = store
        elif settings.store.url:
            self.store = SQLStore(settings.store.url)
        else:
            self.store = MemoryStore()

    @classmethod
    def from_config(cls, path: str | Path | None = None, **kwargs) -> SQLSentry:
        return cls(Settings.load(path), **kwargs)

    # ------------------------------------------------------------------ datasources

    def datasources(self) -> list[str]:
        return list(self.settings.datasources)

    def datasource(self, name: str) -> DataSourceConfig:
        try:
            return self.settings.datasources[name]
        except KeyError:
            raise DataSourceNotFound(f"Unknown datasource '{name}'") from None

    def _db(self, name: str) -> Engine:
        ds = self.datasource(name)
        if not ds.url:
            raise _schema_only(name)
        with self._lock:
            if name not in self._engines:
                self._engines[name] = create_engine(ds.url, pool_pre_ping=True)
            return self._engines[name]

    def schema(self, name: str) -> SchemaCatalog:
        """The policy-filtered schema: exactly what a model is allowed to see."""
        ds = self.datasource(name)

        def load() -> SchemaCatalog:
            if ds.schema_file:
                log.info("loading schema for datasource %s from %s", name, ds.schema_file)
                return load_schema_file(
                    ds.schema_file, datasource=name, dialect=ds.resolved_dialect, policy=ds.policy
                )
            log.info("introspecting datasource %s", name)
            return introspect(
                self._db(name),
                datasource=name,
                dialect=ds.resolved_dialect,
                policy=ds.policy,
                db_schema=ds.db_schema,
                sample_values=ds.sample_values,
            )

        return self._catalogs.get_or_load(name, load)

    def refresh(self, name: str | None = None) -> None:
        self._catalogs.invalidate(name)

    # ------------------------------------------------------------------ providers

    def provider(self, datasource: str, override: str | None = None) -> LLMProvider:
        ds = self.datasource(datasource)
        name = override or ds.llm or self.settings.llm.default
        if not name:
            raise ConfigError("No LLM provider configured (set llm.default).")
        if not ds.policy.provider_allowed(name):
            raise PermissionDenied(f"Datasource '{datasource}' may not be sent to provider '{name}'.")
        with self._lock:
            if name not in self._providers:
                cfg = self.settings.llm.providers.get(name)
                if cfg is None:
                    raise ConfigError(f"Unknown LLM provider '{name}'")
                self._providers[name] = build_provider(name, cfg)
            return self._providers[name]

    # ------------------------------------------------------------------ generate

    def generate(
        self,
        datasource: str,
        question: str,
        *,
        consumer: str | None = None,
        provider: str | None = None,
    ) -> Generation:
        question = (question or "").strip()
        if not question:
            raise BadRequest("Question is empty.")
        if len(question) > MAX_QUESTION_CHARS:
            raise BadRequest(f"Question is longer than {MAX_QUESTION_CHARS} characters.")

        started = time.perf_counter()
        ds = self.datasource(datasource)
        dialect = ds.resolved_dialect
        llm = self.provider(datasource, provider)
        catalog = self.schema(datasource)
        if not catalog.tables:
            raise ConfigError(f"Datasource '{datasource}' exposes no tables; check policy.tables.include.")

        linked = link_tables(question, catalog, self.settings.engine.max_tables_in_prompt)
        shown = {t.lower() for t in linked.table_names()}
        prompt = build_prompt(
            question=question,
            catalog=linked,
            dialect=dialect,
            datasource=datasource,
            description=ds.description,
            glossary=ds.glossary,
            examples=select_examples(question, ds.examples, self.settings.engine.max_examples_in_prompt),
            other_tables=[t for t in catalog.table_names() if t.lower() not in shown],
        )
        messages = list(prompt.messages)
        gen = Generation(
            datasource=datasource,
            question=question,
            consumer=consumer,
            status="failed",
            dialect=dialect,
            provider=llm.name,
            model=llm.model,
            policy_fingerprint=ds.policy.fingerprint(),
            schema_fingerprint=catalog.fingerprint(),
        )
        usage = Usage()
        do_dry_run = self.settings.engine.dry_run and ds.can_connect and supports_dry_run(dialect)
        unverified_note = (
            "Not verified against a database (schema-only datasource)."
            if not ds.can_connect
            else "Not verified against the database."
        )

        for n in range(1, self.settings.engine.max_attempts + 1):
            result = llm.complete(prompt.system, messages, CANDIDATE_JSON_SCHEMA)
            usage.add(result.usage)
            gen.model = result.model or gen.model
            try:
                cand = parse_candidate(result.text)
            except ParseError as e:
                gen.attempts.append(Attempt(number=n, stage="parse", error=str(e)))
                messages += repair_messages(result.text, str(e))
                continue

            if cand.needs_clarification:
                gen.attempts.append(Attempt(number=n, stage="clarification"))
                gen.status = "needs_clarification"
                gen.clarification_question = cand.clarification_question or "Could you clarify the question?"
                gen.explanation = cand.explanation or None
                break

            try:
                guarded = check_sql(cand.sql, catalog=catalog, policy=ds.policy, dialect=dialect)
                if do_dry_run:
                    dry_run(self._db(datasource), guarded.sql, dialect=dialect, timeout_s=ds.policy.timeout_s)
            except (UnsafeSQLError, InvalidSQLError, ExecutionError) as e:
                stage = "verify" if isinstance(e, ExecutionError) else "guard"
                gen.attempts.append(Attempt(number=n, stage=stage, sql=cand.sql, error=e.message))
                messages += repair_messages(result.text, e.message)
                continue

            gen.attempts.append(Attempt(number=n, stage="ok", sql=guarded.sql))
            gen.status = "ok"
            gen.sql = guarded.sql
            gen.explanation = cand.explanation
            gen.tables_used = guarded.tables
            gen.assumptions = cand.assumptions
            gen.warnings = guarded.warnings + ([] if do_dry_run else [unverified_note])
            break

        gen.usage = usage
        gen.latency_ms = int((time.perf_counter() - started) * 1000)
        self.store.save_generation(gen)
        log.info(
            "generation %s datasource=%s status=%s attempts=%d latency_ms=%d",
            gen.id,
            datasource,
            gen.status,
            len(gen.attempts),
            gen.latency_ms,
        )
        if gen.status == "failed":
            raise GenerationFailed(
                f"No valid SQL after {len(gen.attempts)} attempts.",
                details={
                    "generation_id": gen.id,
                    "attempts": [a.model_dump() for a in gen.attempts],
                },
            )
        return gen

    # ------------------------------------------------------------------ validate / execute

    def validate(self, datasource: str, sql: str) -> ValidationResult:
        ds = self.datasource(datasource)
        try:
            r = check_sql(sql, catalog=self.schema(datasource), policy=ds.policy, dialect=ds.resolved_dialect)
        except (UnsafeSQLError, InvalidSQLError) as e:
            return ValidationResult(valid=False, errors=[ValidationIssue(code=e.code, message=e.message)])
        return ValidationResult(valid=True, sql=r.sql, tables=r.tables, warnings=r.warnings)

    def get_generation(self, generation_id: str, *, consumer: str | None = None) -> Generation:
        gen = self.store.get_generation(generation_id)
        if gen is None or (consumer is not None and gen.consumer != consumer):
            raise GenerationNotFound(f"Generation '{generation_id}' not found")
        return gen

    def check_can_execute(self, datasource: str) -> DataSourceConfig:
        """Raise unless SQL may run on this datasource (has a connection and policy allows it)."""
        ds = self.datasource(datasource)
        if not ds.can_connect:
            raise _schema_only(datasource)
        if not ds.policy.allow_execute:
            raise PermissionDenied(f"Execution is disabled for datasource '{datasource}'.")
        return ds

    def ask(
        self,
        datasource: str,
        question: str,
        *,
        consumer: str | None = None,
        provider: str | None = None,
        max_rows: int | None = None,
    ) -> Answer:
        """Batteries included: generate SQL and run it in one call.

        Execution permission is checked *before* the model is called, so a request that can't
        run never spends tokens. If the model asks for clarification, ``result`` is None.
        """
        self.check_can_execute(datasource)
        gen = self.generate(datasource, question, consumer=consumer, provider=provider)
        result = self.execute(gen, consumer=consumer, max_rows=max_rows) if gen.status == "ok" else None
        return Answer(generation=gen, result=result)

    def execute(
        self,
        generation: Generation | str,
        *,
        consumer: str | None = None,
        max_rows: int | None = None,
    ) -> ExecutionResult:
        gen = (
            self.get_generation(generation, consumer=consumer) if isinstance(generation, str) else generation
        )
        if gen.status != "ok" or not gen.sql:
            raise BadRequest(f"Generation '{gen.id}' has no executable SQL (status: {gen.status}).")
        return self.execute_sql(
            gen.datasource, gen.sql, max_rows=max_rows, generation_id=gen.id, consumer=consumer
        )

    def execute_sql(
        self,
        datasource: str,
        sql: str,
        *,
        max_rows: int | None = None,
        generation_id: str | None = None,
        consumer: str | None = None,
    ) -> ExecutionResult:
        """Run SQL read-only: generated SQL, or SQL a user edited. Either way it is re-checked
        against the *current* guard and policy first, so editing can't bypass anything."""
        ds = self.check_can_execute(datasource)
        row_cap = min(max_rows, ds.policy.max_rows) if max_rows else ds.policy.max_rows
        guarded = check_sql(
            sql,
            catalog=self.schema(datasource),
            policy=ds.policy,
            dialect=ds.resolved_dialect,
            max_rows=row_cap,
        )
        columns, rows, truncated, ms = run_query(
            self._db(datasource),
            guarded.sql,
            dialect=ds.resolved_dialect,
            timeout_s=ds.policy.timeout_s,
            max_rows=row_cap,
        )
        result = ExecutionResult(
            generation_id=generation_id,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=ms,
        )
        self.store.save_execution(
            ExecutionRecord(
                id=result.id,
                generation_id=generation_id,
                consumer=consumer,
                row_count=result.row_count,
                truncated=truncated,
                duration_ms=ms,
            )
        )
        return result

    # ------------------------------------------------------------------ feedback

    def feedback(
        self,
        generation_id: str,
        rating: str,
        *,
        corrected_sql: str | None = None,
        comment: str | None = None,
        consumer: str | None = None,
    ) -> Feedback:
        self.get_generation(generation_id, consumer=consumer)
        try:
            fb = Feedback(
                generation_id=generation_id,
                rating=rating,
                corrected_sql=corrected_sql,
                comment=comment,
                consumer=consumer,
            )
        except ValueError as e:
            raise BadRequest(f"Invalid feedback: {e}") from e
        self.store.save_feedback(fb)
        return fb

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        for p in self._providers.values():
            try:
                p.close()
            except Exception:  # noqa: S110 - best-effort cleanup
                pass
        for e in self._engines.values():
            e.dispose()
        self.store.close()

    def __enter__(self) -> SQLSentry:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["SQLSentry", "SQLSentryError"]

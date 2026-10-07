"""SQLAlchemy-backed store. SQLite by default; any SQLAlchemy URL (e.g. Postgres) works."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, create_engine, insert, select
from sqlalchemy.engine import make_url

from ..types import ExecutionRecord, Feedback, Generation
from .base import Store

_meta = MetaData()
_generations = Table(
    "generations",
    _meta,
    Column("id", String(40), primary_key=True),
    Column("consumer", String(200), index=True),
    Column("datasource", String(200), index=True),
    Column("status", String(30)),
    Column("created_at", DateTime(timezone=True), index=True),
    Column("data", Text, nullable=False),
)
_executions = Table(
    "executions",
    _meta,
    Column("id", String(40), primary_key=True),
    Column("generation_id", String(40), index=True),
    Column("created_at", DateTime(timezone=True)),
    Column("data", Text, nullable=False),
)
_feedback = Table(
    "feedback",
    _meta,
    Column("id", String(40), primary_key=True),
    Column("generation_id", String(40), index=True),
    Column("created_at", DateTime(timezone=True), index=True),
    Column("data", Text, nullable=False),
)


class SQLStore(Store):
    def __init__(self, url: str):
        u = make_url(url)
        if u.get_backend_name() == "sqlite" and u.database not in (None, "", ":memory:"):
            Path(u.database).parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(url, pool_pre_ping=True)
        _meta.create_all(self._engine)

    def save_generation(self, gen: Generation) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(_generations).values(
                    id=gen.id,
                    consumer=gen.consumer,
                    datasource=gen.datasource,
                    status=gen.status,
                    created_at=gen.created_at,
                    data=gen.model_dump_json(),
                )
            )

    def get_generation(self, generation_id: str) -> Generation | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(_generations.c.data).where(_generations.c.id == generation_id)).first()
        return Generation.model_validate_json(row[0]) if row else None

    def save_execution(self, record: ExecutionRecord) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(_executions).values(
                    id=record.id,
                    generation_id=record.generation_id,
                    created_at=record.created_at,
                    data=record.model_dump_json(),
                )
            )

    def save_feedback(self, fb: Feedback) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(_feedback).values(
                    id=fb.id,
                    generation_id=fb.generation_id,
                    created_at=fb.created_at,
                    data=fb.model_dump_json(),
                )
            )

    def list_feedback(self, limit: int = 100) -> list[Feedback]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(_feedback.c.data).order_by(_feedback.c.created_at.desc()).limit(limit)
            ).all()
        return [Feedback.model_validate_json(r[0]) for r in rows]

    def close(self) -> None:
        self._engine.dispose()

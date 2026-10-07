"""Connected mode: discover a live database's schema with SQLAlchemy reflection.

Policy is applied while introspecting, so excluded tables and hidden columns are never
read at all, not just filtered afterwards. Sample values come only from visible
text columns.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, String, column, inspect, select, table
from sqlalchemy.exc import SQLAlchemyError

from ..policy import Policy
from .models import Column, ForeignKey, SchemaCatalog, Table

log = logging.getLogger(__name__)

MAX_SAMPLE_LEN = 60


def introspect(
    engine: Engine,
    *,
    datasource: str,
    dialect: str,
    policy: Policy,
    db_schema: str | None = None,
    sample_values: int = 5,
) -> SchemaCatalog:
    insp = inspect(engine)
    names = list(insp.get_table_names(schema=db_schema)) + list(insp.get_view_names(schema=db_schema))
    tables: list[Table] = []
    for name in sorted(set(names)):
        if not policy.table_allowed(name):
            continue
        pk_cols = set((insp.get_pk_constraint(name, schema=db_schema) or {}).get("constrained_columns") or [])
        columns: list[Column] = []
        text_cols: list[str] = []
        for c in insp.get_columns(name, schema=db_schema):
            if policy.column_hidden(name, c["name"]):
                continue
            columns.append(
                Column(
                    name=c["name"],
                    type=_type_name(c["type"]),
                    nullable=bool(c.get("nullable", True)),
                    primary_key=c["name"] in pk_cols,
                    comment=c.get("comment"),
                )
            )
            if isinstance(c["type"], String):
                text_cols.append(c["name"])
        fks = [
            ForeignKey(
                columns=fk["constrained_columns"],
                ref_table=fk["referred_table"],
                ref_columns=fk["referred_columns"],
            )
            for fk in insp.get_foreign_keys(name, schema=db_schema)
            if fk.get("referred_table") and fk.get("constrained_columns")
        ]
        tables.append(
            Table(name=name, comment=_table_comment(insp, name, db_schema), columns=columns, foreign_keys=fks)
        )
        if sample_values:
            _add_samples(engine, tables[-1], text_cols, db_schema, sample_values)

    catalog = SchemaCatalog(datasource=datasource, dialect=dialect, db_schema=db_schema, tables=tables)
    # Re-apply the policy so foreign keys pointing at hidden objects are dropped too.
    return catalog.filtered(policy)


def _type_name(t: object) -> str:
    try:
        return str(t)
    except Exception:  # some dialect types can't compile without a dialect
        return type(t).__name__


def _table_comment(insp, name: str, db_schema: str | None) -> str | None:
    try:
        return (insp.get_table_comment(name, schema=db_schema) or {}).get("text")
    except (NotImplementedError, SQLAlchemyError):
        return None


def _add_samples(engine: Engine, t: Table, text_cols: list[str], db_schema: str | None, n: int) -> None:
    for col_name in text_cols:
        tbl = table(t.name, column(col_name), schema=db_schema)
        stmt = select(tbl.c[col_name]).where(tbl.c[col_name].is_not(None)).distinct().limit(n)
        try:
            with engine.connect() as conn:
                values = [str(v)[:MAX_SAMPLE_LEN] for (v,) in conn.execute(stmt)]
        except SQLAlchemyError as e:
            log.debug("sample values skipped for %s.%s: %s", t.name, col_name, e)
            continue
        col = t.column(col_name)
        if col is not None:
            col.sample_values = values

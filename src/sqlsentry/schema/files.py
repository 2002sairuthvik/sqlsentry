"""Schema-only mode: build the catalog from a file instead of a live database.

Supported files:

* ``.sql``: DDL such as a ``pg_dump --schema-only`` / ``mysqldump --no-data`` export or a
  hand-written file. ``CREATE TABLE`` (columns, types, NOT NULL, primary and foreign keys,
  inline or table-level), ``ALTER TABLE ... ADD CONSTRAINT`` keys, ``COMMENT ON TABLE/COLUMN``
  and MySQL inline ``COMMENT`` are read; everything else (SET, indexes, views, grants...) is
  ignored.
* ``.json``: a bundle written by ``sqlsentry export-context`` (a serialised SchemaCatalog).

The datasource policy is applied exactly as for live introspection.
"""

from __future__ import annotations

from pathlib import Path

import sqlglot
from pydantic import ValidationError
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from ..errors import ConfigError
from ..policy import Policy
from .models import Column, ForeignKey, SchemaCatalog, Table


def load_schema_file(path: str | Path, *, datasource: str, dialect: str, policy: Policy) -> SchemaCatalog:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Schema file not found for datasource '{datasource}': {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        try:
            catalog = SchemaCatalog.model_validate_json(text)
        except ValidationError as e:
            raise ConfigError(f"Invalid schema bundle {p}: {e.errors()[0]['msg']}") from e
        catalog = catalog.model_copy(update={"datasource": datasource, "dialect": dialect})
    else:
        catalog = parse_ddl(text, datasource=datasource, dialect=dialect, source=str(p))
    return catalog.filtered(policy)


def parse_ddl(text: str, *, datasource: str, dialect: str, source: str = "DDL") -> SchemaCatalog:
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except (ParseError, TokenError) as e:
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        raise ConfigError(f"Could not parse {source} as {dialect} DDL: {first}") from e

    tables: dict[str, Table] = {}
    schemas: set[str] = set()

    def table_for(t: exp.Table) -> Table | None:
        return tables.get(t.name.lower())

    for stmt in statements:
        if isinstance(stmt, exp.Create) and (stmt.args.get("kind") or "").upper() == "TABLE":
            schema_node = stmt.this
            if not isinstance(schema_node, exp.Schema) or not isinstance(schema_node.this, exp.Table):
                continue  # CREATE TABLE ... AS SELECT: columns unknown without a database
            tnode = schema_node.this
            if tnode.db:
                schemas.add(tnode.db)
            table = Table(name=tnode.name, comment=_table_comment_property(stmt))
            for item in schema_node.expressions:
                if isinstance(item, exp.ColumnDef):
                    _add_column(table, item)
                else:
                    _apply_constraint(table, item)
            tables[table.name.lower()] = table

        elif isinstance(stmt, exp.Alter) and (stmt.args.get("kind") or "").upper() == "TABLE":
            table = table_for(stmt.this)
            if table is None:
                continue
            for action in stmt.args.get("actions") or []:
                if isinstance(action, exp.AddConstraint):
                    for c in action.expressions:
                        _apply_constraint(table, c)

        elif isinstance(stmt, exp.Comment) and isinstance(stmt.args.get("expression"), exp.Literal):
            kind = (stmt.args.get("kind") or "").upper()
            comment = stmt.args["expression"].this
            if kind == "TABLE" and isinstance(stmt.this, exp.Table):
                table = table_for(stmt.this)
                if table is not None:
                    table.comment = comment
            elif kind == "COLUMN" and isinstance(stmt.this, exp.Column):
                table = tables.get(stmt.this.table.lower())
                col = table.column(stmt.this.name) if table else None
                if col is not None:
                    col.comment = comment

    if not tables:
        raise ConfigError(f"No CREATE TABLE statements found in {source}")
    db_schema = next(iter(schemas)) if len(schemas) == 1 else None
    return SchemaCatalog(
        datasource=datasource, dialect=dialect, db_schema=db_schema, tables=list(tables.values())
    )


def _add_column(table: Table, item: exp.ColumnDef) -> None:
    kind = item.args.get("kind")
    col = Column(name=item.name, type=kind.sql() if kind else "UNKNOWN")
    for constraint in item.args.get("constraints") or []:
        c = constraint.args.get("kind")
        if isinstance(c, exp.NotNullColumnConstraint) and not c.args.get("allow_null"):
            col.nullable = False
        elif isinstance(c, exp.PrimaryKeyColumnConstraint):
            col.primary_key = True
            col.nullable = False
        elif isinstance(c, exp.CommentColumnConstraint) and isinstance(c.this, exp.Literal):
            col.comment = c.this.this
        elif isinstance(c, exp.Reference):
            fk = _reference(c, [col.name])
            if fk:
                table.foreign_keys.append(fk)
    table.columns.append(col)


def _apply_constraint(table: Table, item: exp.Expression) -> None:
    parts = item.expressions if isinstance(item, exp.Constraint) else [item]
    for part in parts:
        if isinstance(part, exp.PrimaryKey):
            for name in _names(part.expressions):
                col = table.column(name)
                if col is not None:
                    col.primary_key = True
                    col.nullable = False
        elif isinstance(part, exp.ForeignKey):
            ref = part.args.get("reference")
            fk = _reference(ref, _names(part.expressions)) if ref is not None else None
            if fk:
                table.foreign_keys.append(fk)


def _reference(ref: exp.Reference, columns: list[str]) -> ForeignKey | None:
    target = ref.this
    if isinstance(target, exp.Schema) and isinstance(target.this, exp.Table):
        ref_table, ref_cols = target.this.name, _names(target.expressions)
    elif isinstance(target, exp.Table):
        ref_table, ref_cols = target.name, []
    else:
        return None
    if not ref_table or not columns:
        return None
    return ForeignKey(columns=columns, ref_table=ref_table, ref_columns=ref_cols or columns)


def _names(nodes: list[exp.Expression]) -> list[str]:
    return [n.name for n in nodes if n.name]


def _table_comment_property(stmt: exp.Create) -> str | None:
    props = stmt.args.get("properties")
    for p in props.expressions if props else []:
        if isinstance(p, exp.SchemaCommentProperty) and isinstance(p.this, exp.Literal):
            return p.this.this
    return None

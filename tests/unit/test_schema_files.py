import json
from pathlib import Path

import pytest

from sqlsentry.config import DataSourceConfig, Settings
from sqlsentry.errors import ConfigError
from sqlsentry.policy import ColumnPolicy, Policy, TablePolicy
from sqlsentry.schema.files import load_schema_file, parse_ddl

OPEN = Policy(tables=TablePolicy(include=["*"]))

PG_DUMP = """
--
-- PostgreSQL database dump
--
SET statement_timeout = 0;
SELECT pg_catalog.set_config('search_path', '', false);

CREATE TABLE public.customers (
    id integer NOT NULL,
    name text NOT NULL,
    email text,
    CONSTRAINT customers_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.customers IS 'People who bought something';
COMMENT ON COLUMN public.customers.name IS 'Full name';

CREATE TABLE public.orders (
    id integer PRIMARY KEY,
    customer_id integer REFERENCES public.customers(id),
    total numeric(10,2)
);
CREATE TABLE public.notes (id integer, order_id integer);
ALTER TABLE ONLY public.notes
    ADD CONSTRAINT notes_order_fk FOREIGN KEY (order_id) REFERENCES public.orders(id);
CREATE INDEX idx_orders_customer ON public.orders USING btree (customer_id);
CREATE VIEW public.v AS SELECT id FROM public.orders;
GRANT SELECT ON public.orders TO reporting;
"""


def test_parses_pg_dump_style_ddl():
    cat = parse_ddl(PG_DUMP, datasource="d", dialect="postgres")
    assert cat.table_names() == ["customers", "orders", "notes"]
    assert cat.db_schema == "public"
    customers = cat.table("customers")
    assert customers.comment == "People who bought something"
    assert customers.column("name").comment == "Full name"
    assert customers.column("id").primary_key and not customers.column("id").nullable
    assert customers.column("email").nullable
    assert cat.table("orders").foreign_keys[0].ref_table == "customers"
    assert cat.table("notes").foreign_keys[0].model_dump() == {
        "columns": ["order_id"],
        "ref_table": "orders",
        "ref_columns": ["id"],
    }


def test_parses_mysql_inline_comments_and_table_fk():
    ddl = """CREATE TABLE users (
        id INT PRIMARY KEY,
        email VARCHAR(100) COMMENT 'login email',
        team_id INT,
        FOREIGN KEY (team_id) REFERENCES teams(id)
    ) COMMENT='application users';"""
    cat = parse_ddl(ddl, datasource="d", dialect="mysql")
    users = cat.table("users")
    assert users.comment == "application users"
    assert users.column("email").comment == "login email"
    assert users.foreign_keys[0].ref_table == "teams"


def test_policy_applies_to_schema_files(tmp_path):
    f = tmp_path / "s.sql"
    f.write_text(PG_DUMP)
    policy = Policy(
        tables=TablePolicy(include=["*"], exclude=["notes"]), columns=ColumnPolicy(hidden=["*.email"])
    )
    cat = load_schema_file(f, datasource="d", dialect="postgres", policy=policy)
    assert cat.table_names() == ["customers", "orders"]
    assert cat.table("customers").column("email") is None


def test_default_deny_applies_to_schema_files(tmp_path):
    f = tmp_path / "s.sql"
    f.write_text(PG_DUMP)
    assert load_schema_file(f, datasource="d", dialect="postgres", policy=Policy()).tables == []


def test_json_bundle_round_trip(tmp_path, store_catalog):
    f = tmp_path / "bundle.json"
    f.write_text(store_catalog.model_dump_json())
    cat = load_schema_file(f, datasource="renamed", dialect="sqlite", policy=OPEN)
    assert cat.datasource == "renamed"
    assert cat.table_names() == store_catalog.table_names()
    assert cat.table("customers").column("country").sample_values  # samples survive the bundle


@pytest.mark.parametrize(
    "content,match",
    [("CREATE INDEX i ON t (a);", "No CREATE TABLE"), ("CREATE TABLE (((", "Could not parse")],
)
def test_bad_schema_files(tmp_path, content, match):
    f = tmp_path / "bad.sql"
    f.write_text(content)
    with pytest.raises(ConfigError, match=match):
        load_schema_file(f, datasource="d", dialect="postgres", policy=OPEN)


def test_missing_and_invalid_bundle(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_schema_file(tmp_path / "nope.sql", datasource="d", dialect="postgres", policy=OPEN)
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"tables": "nope"}))
    with pytest.raises(ConfigError, match="Invalid schema bundle"):
        load_schema_file(f, datasource="d", dialect="postgres", policy=OPEN)


def test_datasource_config_validation():
    with pytest.raises(ValueError, match="url"):
        DataSourceConfig()
    with pytest.raises(ValueError, match="dialect"):
        DataSourceConfig(schema_file="x.sql")
    ds = DataSourceConfig(schema_file="x.sql", dialect="postgres")
    assert ds.mode == "schema_only" and not ds.can_connect
    assert DataSourceConfig(url="sqlite:///x.db").mode == "connected"


def test_schema_file_path_is_relative_to_config(tmp_path):
    cfg_dir = tmp_path / "conf"
    cfg_dir.mkdir()
    (cfg_dir / "sqlsentry.yaml").write_text(
        "datasources:\n  w:\n    schema_file: schema/w.sql\n    dialect: postgres\n"
    )
    s = Settings.from_yaml(cfg_dir / "sqlsentry.yaml")
    assert Path(s.datasources["w"].schema_file) == (cfg_dir / "schema" / "w.sql").resolve()

from sqlalchemy import create_engine

from sqlsentry.config import FewShotExample
from sqlsentry.policy import Consumer, Grant, Policy, TablePolicy
from sqlsentry.schema.introspect import introspect
from sqlsentry.schema.linker import link_tables, select_examples, tokens


def test_introspection_applies_policy(store_catalog):
    names = store_catalog.table_names()
    assert set(names) == {"customers", "orders", "order_items", "products"}
    customers = store_catalog.table("customers")
    assert customers.column("email") is None
    assert customers.column("id").primary_key
    assert customers.column("country").sample_values  # text columns get samples


def test_foreign_keys_discovered(store_catalog):
    fks = store_catalog.table("orders").foreign_keys
    assert any(fk.ref_table == "customers" and fk.columns == ["customer_id"] for fk in fks)


def test_default_policy_denies_everything(sample_db_url):
    engine = create_engine(sample_db_url)
    catalog = introspect(engine, datasource="store", dialect="sqlite", policy=Policy())
    engine.dispose()
    assert catalog.tables == []


def test_policy_wildcards_case_insensitive():
    p = Policy(tables=TablePolicy(include=["Order*"]))
    assert p.table_allowed("orders") and p.table_allowed("ORDER_ITEMS")
    assert not p.table_allowed("customers")


def test_fingerprints_are_stable(store_catalog, store_policy):
    assert store_catalog.fingerprint() == store_catalog.model_copy(deep=True).fingerprint()
    assert store_policy.fingerprint() == Policy.model_validate(store_policy.model_dump()).fingerprint()


def test_consumer_scopes():
    c = Consumer(name="t", grants=[Grant(datasource="store", scopes=["generate"]), Grant(datasource="*")])
    assert c.scopes_for("store") == {"generate", "validate", "schema"}
    assert c.scopes_for("other") == {"generate", "validate", "schema"}
    assert Consumer(name="x").scopes_for("store") == set()


def test_tokens():
    assert tokens("Top customers by orderDate") == {"customer", "order", "date"}


def test_linker_keeps_small_schemas_whole(store_catalog):
    assert link_tables("anything", store_catalog, max_tables=10) is store_catalog


def test_linker_picks_relevant_tables_and_join_partners(store_catalog):
    linked = link_tables("which products sell the most", store_catalog, max_tables=2)
    assert "products" in linked.table_names()
    assert len(linked.tables) == 2  # products + its FK partner order_items


def test_select_examples():
    ex = [
        FewShotExample(question="revenue by country", sql="SELECT 1"),
        FewShotExample(question="number of products", sql="SELECT 2"),
    ]
    assert select_examples("total revenue per country", ex, k=1)[0].sql == "SELECT 1"
    assert select_examples("x", ex, k=0) == []

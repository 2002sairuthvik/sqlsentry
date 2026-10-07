"""A small, deterministic, fictional store database used by tests, docs and the demo.

All names and emails are generated; nothing is real.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    country TEXT NOT NULL,
    city TEXT NOT NULL,
    segment TEXT NOT NULL,
    signup_date DATE NOT NULL
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date DATE NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE internal_audit_log (
    id INTEGER PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_FIRST = ["Asha", "Ben", "Chen", "Diego", "Elif", "Farah", "Gustav", "Hana", "Ivan", "Jade", "Kofi", "Lena"]
_LAST = [
    "Rao",
    "Smith",
    "Li",
    "Garcia",
    "Yilmaz",
    "Khan",
    "Berg",
    "Sato",
    "Petrov",
    "Moreau",
    "Mensah",
    "Novak",
]
_PLACES = [
    ("US", "New York"), ("US", "Austin"), ("IN", "Hyderabad"), ("IN", "Bengaluru"), ("DE", "Berlin"),
    ("GB", "London"), ("JP", "Tokyo"), ("BR", "Sao Paulo"), ("CA", "Toronto"), ("FR", "Paris"),
]  # fmt: skip
_CATALOG = {
    "Electronics": [
        "Wireless Mouse",
        "USB-C Hub",
        "Mechanical Keyboard",
        "27in Monitor",
        "Webcam HD",
        "Headphones",
    ],
    "Books": ["SQL Basics", "Data Engineering 101", "Clean Code Notes", "Python Recipes"],
    "Home": ["Desk Lamp", "Office Chair", "Standing Desk", "Coffee Mug", "Plant Pot"],
    "Sports": ["Yoga Mat", "Running Shoes", "Water Bottle", "Dumbbell Set", "Cycling Gloves"],
}
_SEGMENTS = ["consumer", "consumer", "consumer", "business", "enterprise"]
_STATUSES = ["delivered", "delivered", "delivered", "shipped", "pending", "cancelled"]


def create_sample_db(path: str | Path, *, seed: int = 42, customers: int = 120, orders: int = 900) -> Path:
    """(Re)create the sample database at ``path`` and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(seed)  # noqa: S311 - deterministic fake data, not security
    start = date(2024, 1, 1)

    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        for cid in range(1, customers + 1):
            first, last = rng.choice(_FIRST), rng.choice(_LAST)
            country, city = rng.choice(_PLACES)
            con.execute(
                "INSERT INTO customers VALUES (?,?,?,?,?,?,?)",
                (
                    cid,
                    f"{first} {last}",
                    f"{first.lower()}.{last.lower()}{cid}@example.com",
                    country,
                    city,
                    rng.choice(_SEGMENTS),
                    (start + timedelta(days=rng.randint(0, 500))).isoformat(),
                ),
            )
        pid = 0
        prices: dict[int, float] = {}
        for category, names in _CATALOG.items():
            for name in names:
                pid += 1
                prices[pid] = round(rng.uniform(5, 400), 2)
                con.execute("INSERT INTO products VALUES (?,?,?,?)", (pid, name, category, prices[pid]))
        item_id = 0
        for oid in range(1, orders + 1):
            con.execute(
                "INSERT INTO orders VALUES (?,?,?,?)",
                (
                    oid,
                    rng.randint(1, customers),
                    (start + timedelta(days=rng.randint(0, 640))).isoformat(),
                    rng.choice(_STATUSES),
                ),
            )
            for product in rng.sample(sorted(prices), rng.randint(1, 4)):
                item_id += 1
                con.execute(
                    "INSERT INTO order_items VALUES (?,?,?,?,?)",
                    (item_id, oid, product, rng.randint(1, 5), prices[product]),
                )
        con.execute(
            "INSERT INTO internal_audit_log VALUES (1, 'admin', 'rotated credentials', '2025-01-01T00:00:00')"
        )
        con.commit()
    finally:
        con.close()
    return path

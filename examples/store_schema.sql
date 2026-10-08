-- The sample store as plain PostgreSQL DDL: an example of a schema-only datasource.
-- sqlsentry reads this file to generate SQL; it never connects to a database for it.

CREATE TABLE customers (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    email       text NOT NULL,
    country     varchar(2) NOT NULL,
    city        text NOT NULL,
    segment     text NOT NULL,
    signup_date date NOT NULL
);
COMMENT ON TABLE customers IS 'People who have an account in the store';
COMMENT ON COLUMN customers.country IS 'ISO 3166-1 alpha-2 code, e.g. US, IN, DE';
COMMENT ON COLUMN customers.segment IS 'One of: consumer, business, enterprise';

CREATE TABLE products (
    id         integer PRIMARY KEY,
    name       text NOT NULL,
    category   text NOT NULL,
    unit_price numeric(10, 2) NOT NULL
);
COMMENT ON COLUMN products.category IS 'One of: Electronics, Books, Home, Sports';

CREATE TABLE orders (
    id          integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers (id),
    order_date  date NOT NULL,
    status      text NOT NULL
);
COMMENT ON COLUMN orders.status IS 'One of: pending, shipped, delivered, cancelled';

CREATE TABLE order_items (
    id         integer PRIMARY KEY,
    order_id   integer NOT NULL,
    product_id integer NOT NULL,
    quantity   integer NOT NULL,
    unit_price numeric(10, 2) NOT NULL,
    CONSTRAINT order_items_order_fk FOREIGN KEY (order_id) REFERENCES orders (id)
);
ALTER TABLE order_items ADD CONSTRAINT order_items_product_fk FOREIGN KEY (product_id) REFERENCES products (id);
COMMENT ON COLUMN order_items.unit_price IS 'Price per unit at the time of the order';

CREATE TABLE internal_audit_log (
    id         integer PRIMARY KEY,
    actor      text NOT NULL,
    action     text NOT NULL,
    created_at timestamptz NOT NULL
);

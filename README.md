# sqlsentry

**Safety-first, model-agnostic natural language to SQL.**
Ask a question in plain English, get back commented SQL and an explanation of what it does.
Optionally run it read-only and get the rows. Use it as a Python library, a CLI, or a REST API.

```text
$ sqlsentry ask store "top 5 customers by revenue" --execute

/* Top 5 customers by revenue (delivered or shipped orders only, per the glossary) */
SELECT
  c.id,
  c.name,
  SUM(oi.quantity * oi.unit_price) AS revenue
FROM customers AS c
JOIN orders AS o
  ON o.customer_id = c.id /* the customer who placed each order */
JOIN order_items AS oi
  ON oi.order_id = o.id
WHERE
  o.status IN ('delivered', 'shipped')
GROUP BY
  c.id,
  c.name
ORDER BY
  revenue DESC
LIMIT 5

Explanation: Adds up what each customer spent on delivered or shipped orders and lists the five biggest spenders.

id   name          revenue
---  ------------  --------
52   Ben Mensah    19359.02
57   Ben Sato      17446.68
102  Elif Rao      16504.29
25   Farah Garcia  16161.64
89   Chen Rao      16159.56
```

## Why sqlsentry

Text-to-SQL is easy to demo and hard to trust. sqlsentry focuses on the trust part.

- **Every query passes a guard before it is returned or run.**
  - Layer 1 is hard-coded and can't be switched off: one read-only `SELECT`, no writes or DDL, no stacked statements, no row locks, and no functions that sleep, touch files or run dynamic SQL.
  - Layer 2 is your policy: which tables and columns are visible, the row cap, the timeout, and whether execution is allowed at all.
- **Hidden data never reaches the model.** Tables and columns your policy hides are left out of discovery, so they never appear in prompts. `SELECT *` is expanded to visible columns only, and query results are never sent to the LLM.
- **Any model.**
  - Free cloud tiers (Groq), fully local open-source models (Ollama, vLLM), OpenAI-compatible APIs, Claude, or your own provider class.
  - The policy can restrict which providers a datasource's schema may be sent to.
- **Any database** SQLAlchemy can connect to. SQL is generated and validated in the right dialect with [sqlglot](https://github.com/tobymao/sqlglot).
- **Self-correcting.** When the guard or the database (via `EXPLAIN`) rejects a query, the error goes back to the model to fix, up to 3 attempts.
- **Measurable.** `sqlsentry eval` runs a golden question set and compares *result sets*, so you can choose models and prompts based on accuracy, not vibes.

## Quickstart

```bash
pip install "sqlsentry[server]"      # until the first PyPI release: pip install -e ".[server]" from a clone
mkdir demo && cd demo
sqlsentry init                       # creates sqlsentry.yaml + a fictional sample store database
```

Pick a model:

- **Groq (free tier, cloud):** get a key at <https://console.groq.com/keys>, then `export GROQ_API_KEY=...` (or put it in a `.env` file).
- **Ollama (free, fully local and private):** `ollama pull qwen2.5-coder:7b`, then set `llm.default: local` in `sqlsentry.yaml`.

Then:

```bash
sqlsentry inspect store                                   # exactly what the model is allowed to see
sqlsentry ask store "which category brings in the most revenue?" --execute
sqlsentry validate store "SELECT email FROM customers"    # rejected: hidden column
```

### Open in GitHub Codespaces

The repo includes a dev container: open it in a Codespace, add `GROQ_API_KEY` as a Codespaces secret, and everything is installed with the sample database ready.

## Use it as a library

```python
from sqlsentry import SQLSentry

with SQLSentry.from_config("sqlsentry.yaml") as sentry:
    gen = sentry.generate("store", "how many orders are still pending?")
    if gen.status == "ok":
        print(gen.sql)
        print(gen.explanation)
        result = sentry.execute(gen)  # only if the datasource policy allows execution
        print(result.columns, result.rows)
    elif gen.status == "needs_clarification":
        print("Question:", gen.clarification_question)
```

## Run the REST API

```bash
sqlsentry hash-key            # prints a new API key and the digest to put in sqlsentry.yaml
sqlsentry serve               # http://127.0.0.1:8000, interactive docs at /docs
```

```bash
curl -s localhost:8000/v1/sql/generate \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"datasource": "store", "question": "orders per status"}'
```

| Endpoint | Purpose |
|---|---|
| `POST /v1/sql/generate` | Question → SQL, explanation, assumptions (or a clarifying question) |
| `POST /v1/sql/execute` | Run a generation's SQL read-only (scope + policy permitting) |
| `POST /v1/sql/validate` | Check any SQL against the guard and policy |
| `GET /v1/generations/{id}` | Fetch a past generation (yours only) |
| `POST /v1/generations/{id}/feedback` | Mark correct/incorrect, submit corrected SQL |
| `GET /v1/datasources` | Datasources your key can use |
| `GET /v1/datasources/{id}/schema` | The policy-filtered schema |

Each API key belongs to a consumer with per-datasource scopes (`generate`, `execute`, `validate`, `schema`). Datasources a key isn't granted return 404, exactly like ones that don't exist. There's a thin Python client in `sqlsentry.client`. Docker: `docker compose up` (see `docker-compose.yml`).

## Configuration at a glance

```yaml
datasources:
  sales:
    url: postgresql+psycopg://readonly_user:${DB_PASSWORD}@db:5432/sales   # a read-only user
    policy:
      tables: {include: ["orders", "customers", "products"], exclude: ["*_audit"]}
      columns: {hidden: ["customers.email", "*.ssn"]}
      max_rows: 1000
      allow_execute: false
      allowed_providers: ["local"]      # this schema never leaves your network
    glossary:
      - "active customer = placed an order in the last 90 days"
    examples:
      - question: "revenue last month"
        sql: "SELECT SUM(total) FROM orders WHERE ..."
```

See [docs/configuration.md](docs/configuration.md) for every option and [docs/security-model.md](docs/security-model.md) for what is and isn't guaranteed.

## Measuring accuracy

```bash
sqlsentry eval evals/datasets/sample_store.yaml                 # default model
sqlsentry eval evals/datasets/sample_store.yaml --provider local
```

Write your own dataset of real questions plus verified SQL. It's the best way to pick a model and to catch regressions (`--min-accuracy 0.8` fails CI below 80%).

## Project status and roadmap

v0.1 is in development. Planned next:

- Upload mode: `sqlsentry export-context` to build a schema bundle inside a private network
- Admin API for datasources, policies and keys
- Row-level policy filters
- Embedding-based schema linking for very large schemas
- Async execution for long-running warehouse queries
- PyPI and container image releases

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

[Apache 2.0](LICENSE)

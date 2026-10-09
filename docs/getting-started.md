# Getting started

## 1. Install

```bash
pip install "sqlsentry[server] @ git+https://github.com/2002sairuthvik/sqlsentry@v0.1.0"
```

From a clone instead: `pip install -e ".[dev]"`. Or open the repo in GitHub Codespaces, where
everything is preinstalled.

## 2. Create a config and sample database

```bash
sqlsentry init
```

This writes `sqlsentry.yaml` and `store.db`, a fictional store with customers, products, orders and
order items. One table (`internal_audit_log`) and one column (`customers.email`) are hidden by the
policy, so you can see the guard at work.

## 3. Choose a model

- Groq free tier: set `GROQ_API_KEY` (env var or `.env`). This is the default in the generated config.
- Local: install [Ollama](https://ollama.com), `ollama pull qwen2.5-coder:7b`, set `llm.default: local`.

## 4. Ask

```bash
sqlsentry inspect store
sqlsentry ask store "how many orders are still pending?" --execute
sqlsentry ask store "show customer emails"          # the model can't see emails
sqlsentry validate store "DELETE FROM orders"       # rejected by the locked baseline
```

## 5. Try it on your own schema, no connection needed

Export your schema (e.g. `pg_dump --schema-only mydb > schema.sql`) and add:

```yaml
datasources:
  mine:
    schema_file: schema.sql
    dialect: postgres
    policy:
      tables: {include: ["*"]}
```

```bash
sqlsentry inspect mine
sqlsentry ask mine "your question"
```

You get SQL to review and run yourself; sqlsentry never touches the database.

## 6. Point it at your database

Add a datasource with a **read-only** user, start with a narrow `tables.include`, hide sensitive
columns, add a glossary and a few verified examples, then measure:

```bash
sqlsentry eval my_questions.yaml
```

## 7. Serve it to other teams

```bash
sqlsentry hash-key       # one key per consumer; put the digest under consumers:
sqlsentry serve --host 0.0.0.0
```

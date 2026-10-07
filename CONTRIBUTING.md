# Contributing

Thanks for your interest in sqlsentry!

## Setup

The fastest way is GitHub Codespaces: open the repo in a Codespace and everything is
installed for you. Locally:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install                 # optional
```

## Checks

```bash
ruff check .
ruff format .
pytest -q
```

Tests use a scripted fake LLM and a generated SQLite database, so they need no API key
and no network. PostgreSQL tests run only when `SQLSENTRY_TEST_PG_URL` is set.

## Guidelines

- **Safety first.** Changes to `guard/`, `policy.py` or `execution/` need tests for the
  rejection path. The Layer 1 baseline must never become configurable.
- Keep the core (`sqlsentry/*` except `server/`) free of web-framework imports so it
  stays usable as a plain library.
- New LLM providers implement `sqlsentry.llm.base.LLMProvider` and register in
  `sqlsentry/llm/registry.py`.
- New databases usually need nothing beyond a SQLAlchemy driver. If dry-run `EXPLAIN`
  syntax differs, add it to `sqlsentry/execution/verify.py`.

## Commit style

Short imperative subject line ("Add MySQL dry-run support"), with details in the body
if needed.

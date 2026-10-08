# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Core engine: schema introspection, schema linking, prompt building, repair loop.
- Two-layer SQL guard: locked read-only baseline + configurable policy.
- Model-agnostic LLM layer: OpenAI-compatible (Groq, Ollama, vLLM, OpenAI, ...), Anthropic, and a fake provider for tests.
- REST API with API keys, per-datasource grants and rate limiting.
- CLI: `init`, `inspect`, `ask`, `validate`, `serve`, `eval`, `hash-key`, `sample-db`.
- Eval harness comparing result sets, plus a 25-question golden dataset for the sample store.
- Docker image and compose file (Groq by default, optional local Ollama profile).
- Docs: getting started, configuration, providers, security model, architecture.
- Two ways to use it: SQL only, or batteries included (SQL + rows).
  - Schema-only datasources: `schema_file` (DDL or JSON bundle) instead of a database URL.
  - `sqlsentry export-context` writes a datasource's policy-filtered schema to a JSON bundle.
  - `SQLSentry.ask()`, `POST /v1/sql/query` and `ask --execute`: generate and run in one call;
    execution permission is checked before the model is called.
  - `POST /v1/sql/execute` and `execute_sql()` also run user-edited SQL, under the same guard.
  - `GET /v1/datasources` reports each datasource's `mode` and `can_execute`.

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
- CLI: `init`, `inspect`, `ask`, `serve`, `eval`, `hash-key`, `sample-db`.

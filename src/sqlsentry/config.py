"""Configuration, loaded from YAML or built in code.

``${VAR}`` and ``${VAR:-default}`` in any string are expanded from the environment, so
connection strings and keys never have to live in the file. See ``examples/sqlsentry.yaml``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from .errors import ConfigError
from .policy import Consumer, Policy

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class FewShotExample(BaseModel):
    """A verified question -> SQL pair. The biggest single accuracy lever for a schema."""

    question: str
    sql: str


class DataSourceConfig(BaseModel):
    """A database sqlsentry can generate SQL for.

    * ``url`` only: connected mode (schema introspected live; SQL can be dry-run and executed).
    * ``schema_file`` only: schema-only mode (SQL is generated and validated, never run).
    * both: schema from the file, execution through the connection.
    """

    url: str | None = Field(None, description="SQLAlchemy URL. Use a read-only database user.")
    schema_file: str | None = Field(
        None,
        description="DDL (.sql) or `sqlsentry export-context` bundle (.json); relative to the config file.",
    )
    schema_ddl: str | None = Field(
        None, description="Inline DDL (CREATE TABLE ...) instead of a schema file."
    )
    dialect: str | None = Field(None, description="sqlglot dialect; inferred from the URL if omitted.")
    description: str = ""
    db_schema: str | None = Field(None, description="Database schema/namespace to introspect.")
    policy: Policy = Field(default_factory=Policy)
    glossary: list[str] = Field(default_factory=list, description="Business definitions for the LLM.")
    examples: list[FewShotExample] = Field(default_factory=list)
    sample_values: int = Field(5, ge=0, le=50, description="Distinct sample values per text column.")
    llm: str | None = Field(None, description="Provider name override for this datasource.")

    @model_validator(mode="after")
    def _check_source(self) -> DataSourceConfig:
        if not self.url and not self.schema_file and not self.schema_ddl:
            raise ValueError(
                "a datasource needs 'url' (connected) and/or 'schema_file' / 'schema_ddl' (schema-only)"
            )
        if self.schema_file and self.schema_ddl:
            raise ValueError("use either 'schema_file' or 'schema_ddl', not both")
        if not self.url and not self.dialect:
            raise ValueError("schema-only datasources need an explicit 'dialect' (e.g. postgres)")
        return self

    @property
    def resolved_dialect(self) -> str:
        return self.dialect or dialect_from_url(self.url or "")

    @property
    def can_connect(self) -> bool:
        return bool(self.url)

    @property
    def mode(self) -> str:
        return "connected" if self.url else "schema_only"


class ProviderConfig(BaseModel):
    type: str = Field(description="openai_compatible | anthropic | fake | 'module:Class'")
    model: str = ""
    base_url: str | None = None
    api_key_env: str | None = Field(None, description="Name of the env var holding the key.")
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_s: float = 60.0
    max_retries: int = 2
    json_mode: bool = True
    options: dict[str, Any] = Field(default_factory=dict, description="Provider-specific extras.")

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


class LLMSettings(BaseModel):
    default: str | None = None
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_default(self) -> LLMSettings:
        if self.default is None and len(self.providers) == 1:
            self.default = next(iter(self.providers))
        if self.default is not None and self.default not in self.providers:
            raise ValueError(f"llm.default '{self.default}' is not in llm.providers")
        return self


class EngineSettings(BaseModel):
    max_attempts: int = Field(3, ge=1, le=10)
    max_tables_in_prompt: int = Field(15, ge=1)
    max_examples_in_prompt: int = Field(3, ge=0)
    dry_run: bool = Field(True, description="EXPLAIN generated SQL against the DB before returning it.")
    schema_cache_ttl_s: int = Field(600, ge=0)


class PlaygroundSettings(BaseModel):
    """The browser playground at /playground. Off by default; it needs no API key, so it has its
    own per-visitor rate limit and only exposes the datasources listed here."""

    enabled: bool = False
    datasources: list[str] = Field(default_factory=list, description="Shown in the playground; empty = all.")
    allow_custom_schema: bool = Field(True, description="Visitors may paste DDL (never executed).")
    custom_schema_max_chars: int = Field(50_000, ge=1000)
    requests_per_minute: int = Field(10, ge=0, description="Per visitor IP. 0 disables.")
    max_rows: int = Field(50, ge=1)
    sample_questions: dict[str, list[str]] = Field(
        default_factory=dict, description="Clickable example questions per datasource."
    )
    trust_proxy_headers: bool = Field(
        False, description="Use X-Forwarded-For for the visitor IP (enable only behind a trusted proxy)."
    )


class ServerSettings(BaseModel):
    auth_enabled: bool = True
    rate_limit_per_minute: int = Field(60, ge=0, description="Per consumer. 0 disables.")
    cors_origins: list[str] = Field(default_factory=list)
    playground: PlaygroundSettings = Field(default_factory=PlaygroundSettings)


class StoreSettings(BaseModel):
    url: str | None = Field(
        "sqlite:///.sqlsentry/history.db",
        description="Where generations/feedback are kept. None = in memory only.",
    )


class Settings(BaseModel):
    datasources: dict[str, DataSourceConfig] = Field(default_factory=dict)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    engine: EngineSettings = Field(default_factory=EngineSettings)
    consumers: list[Consumer] = Field(default_factory=list)
    server: ServerSettings = Field(default_factory=ServerSettings)
    store: StoreSettings = Field(default_factory=StoreSettings)

    @classmethod
    def from_yaml(cls, path: str | os.PathLike[str]) -> Settings:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"Config file not found: {p}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        try:
            settings = cls.model_validate(expand_env(raw))
        except ValidationError as e:
            raise ConfigError(f"Invalid config {p}:\n{e}") from e
        # Every relative path in the file is relative to the file itself, not to the current
        # directory: MCP clients and services start sqlsentry from unpredictable directories.
        base = p.resolve().parent
        for ds in settings.datasources.values():
            if ds.schema_file and not Path(ds.schema_file).is_absolute():
                ds.schema_file = str((base / ds.schema_file).resolve())
            if ds.url:
                ds.url = resolve_sqlite_url(ds.url, base)
        if settings.store.url:
            settings.store.url = resolve_sqlite_url(settings.store.url, base)
        return settings

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> Settings:
        """Load from ``path``, else ``$SQLSENTRY_CONFIG``, else ``./sqlsentry.yaml``."""
        return cls.from_yaml(path or os.environ.get("SQLSENTRY_CONFIG", "sqlsentry.yaml"))


def resolve_sqlite_url(url: str, base: Path) -> str:
    """Make a relative SQLite file path absolute against ``base``; other URLs are unchanged."""
    if not url.lower().startswith("sqlite"):
        return url
    from sqlalchemy.engine import make_url

    u = make_url(url)
    db = u.database
    if not db or db == ":memory:" or db.startswith("file:") or Path(db).is_absolute():
        return url
    return u.set(database=(base / db).resolve().as_posix()).render_as_string(hide_password=False)


_URL_DIALECTS = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
    "mssql": "tsql",
    "oracle": "oracle",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "duckdb": "duckdb",
    "redshift": "redshift",
    "trino": "trino",
    "clickhouse": "clickhouse",
    "databricks": "databricks",
}


def dialect_from_url(url: str) -> str:
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    if scheme not in _URL_DIALECTS:
        raise ConfigError(f"Cannot infer SQL dialect from '{scheme}://'; set 'dialect' explicitly.")
    return _URL_DIALECTS[scheme]


def expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ConfigError(f"Environment variable {name} is not set")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value

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
    url: str = Field(description="SQLAlchemy URL. Use a read-only database user.")
    dialect: str | None = Field(None, description="sqlglot dialect; inferred from the URL if omitted.")
    description: str = ""
    db_schema: str | None = Field(None, description="Database schema/namespace to introspect.")
    policy: Policy = Field(default_factory=Policy)
    glossary: list[str] = Field(default_factory=list, description="Business definitions for the LLM.")
    examples: list[FewShotExample] = Field(default_factory=list)
    sample_values: int = Field(5, ge=0, le=50, description="Distinct sample values per text column.")
    llm: str | None = Field(None, description="Provider name override for this datasource.")

    @property
    def resolved_dialect(self) -> str:
        return self.dialect or dialect_from_url(self.url)


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


class ServerSettings(BaseModel):
    auth_enabled: bool = True
    rate_limit_per_minute: int = Field(60, ge=0, description="Per consumer. 0 disables.")
    cors_origins: list[str] = Field(default_factory=list)


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
            return cls.model_validate(expand_env(raw))
        except ValidationError as e:
            raise ConfigError(f"Invalid config {p}:\n{e}") from e

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> Settings:
        """Load from ``path``, else ``$SQLSENTRY_CONFIG``, else ``./sqlsentry.yaml``."""
        return cls.from_yaml(path or os.environ.get("SQLSENTRY_CONFIG", "sqlsentry.yaml"))


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

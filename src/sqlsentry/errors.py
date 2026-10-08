"""Exception hierarchy.

Everything sqlsentry raises derives from :class:`SQLSentryError`, so library users can
catch one type. Each error carries a stable machine-readable ``code`` and the HTTP status
the REST API maps it to.
"""

from __future__ import annotations

from typing import Any


class SQLSentryError(Exception):
    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigError(SQLSentryError):
    code = "config_error"


class BadRequest(SQLSentryError):
    code = "bad_request"
    http_status = 400


class AuthError(SQLSentryError):
    code = "unauthorized"
    http_status = 401


class PermissionDenied(SQLSentryError):
    code = "forbidden"
    http_status = 403


class NotFound(SQLSentryError):
    code = "not_found"
    http_status = 404


class DataSourceNotFound(NotFound):
    code = "datasource_not_found"


class GenerationNotFound(NotFound):
    code = "generation_not_found"


class RateLimited(SQLSentryError):
    code = "rate_limited"
    http_status = 429


class UnsafeSQLError(SQLSentryError):
    """SQL broke a safety rule: writes, multiple statements, forbidden functions or objects."""

    code = "unsafe_sql"
    http_status = 422


class InvalidSQLError(SQLSentryError):
    """SQL is not unsafe but cannot be used: parse errors, unknown tables or columns."""

    code = "invalid_sql"
    http_status = 422


class GenerationFailed(SQLSentryError):
    """No valid SQL was produced within the attempt budget."""

    code = "generation_failed"
    http_status = 422


class LLMError(SQLSentryError):
    code = "llm_error"
    http_status = 502


class ExecutionUnavailable(SQLSentryError):
    """The datasource is schema-only: SQL can be generated and validated but not run here."""

    code = "execution_unavailable"
    http_status = 409


class ExecutionError(SQLSentryError):
    code = "execution_error"
    http_status = 422

"""Thin Python client for a sqlsentry server.

from sqlsentry.client import SQLSentryClient
with SQLSentryClient("https://sqlsentry.internal", api_key="sqs_...") as c:
    gen = c.generate("store", "top 5 customers by revenue")
    print(gen["sql"])
"""

from __future__ import annotations

from typing import Any

import httpx

from .errors import SQLSentryError


class SQLSentryAPIError(SQLSentryError):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, details=details)
        self.http_status = status
        self.code = code


class SQLSentryClient:
    def __init__(self, base_url: str, api_key: str | None = None, *, timeout: float = 120.0, **httpx_kwargs):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, **httpx_kwargs
        )

    def _call(self, method: str, path: str, **kwargs) -> Any:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                err = resp.json()["error"]
            except (ValueError, KeyError, TypeError):
                raise SQLSentryAPIError(resp.status_code, "http_error", resp.text[:300]) from None
            raise SQLSentryAPIError(
                resp.status_code, err.get("code", "error"), err.get("message", ""), err.get("details")
            )
        return resp.json()

    def generate(self, datasource: str, question: str, *, provider: str | None = None) -> dict[str, Any]:
        body = {"datasource": datasource, "question": question}
        if provider:
            body["provider"] = provider
        return self._call("POST", "/v1/sql/generate", json=body)

    def query(
        self, datasource: str, question: str, *, provider: str | None = None, max_rows: int | None = None
    ) -> dict[str, Any]:
        """Generate SQL and run it in one call; the response has the SQL plus ``result``."""
        body = {"datasource": datasource, "question": question, "provider": provider, "max_rows": max_rows}
        return self._call("POST", "/v1/sql/query", json=body)

    def execute(self, generation_id: str, *, max_rows: int | None = None) -> dict[str, Any]:
        return self._call(
            "POST", "/v1/sql/execute", json={"generation_id": generation_id, "max_rows": max_rows}
        )

    def execute_sql(self, datasource: str, sql: str, *, max_rows: int | None = None) -> dict[str, Any]:
        """Run SQL you wrote or edited; it is still checked by the guard and policy."""
        body = {"datasource": datasource, "sql": sql, "max_rows": max_rows}
        return self._call("POST", "/v1/sql/execute", json=body)

    def validate(self, datasource: str, sql: str) -> dict[str, Any]:
        return self._call("POST", "/v1/sql/validate", json={"datasource": datasource, "sql": sql})

    def generation(self, generation_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/generations/{generation_id}")

    def feedback(
        self, generation_id: str, rating: str, *, corrected_sql: str | None = None, comment: str | None = None
    ) -> dict[str, Any]:
        body = {"rating": rating, "corrected_sql": corrected_sql, "comment": comment}
        return self._call("POST", f"/v1/generations/{generation_id}/feedback", json=body)

    def datasources(self) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/datasources")

    def schema(self, datasource: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/datasources/{datasource}/schema")

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SQLSentryClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

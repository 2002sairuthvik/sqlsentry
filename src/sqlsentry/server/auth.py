"""API-key authentication and grant checks.

Keys are never stored, only their SHA-256 digests (``sqlsentry hash-key``). A consumer
that has no grant on a datasource gets 404 for it, so datasources it can't use are
indistinguishable from ones that don't exist.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from ..config import Settings
from ..errors import AuthError, DataSourceNotFound, PermissionDenied
from ..policy import ALL_SCOPES, Consumer

KEY_PREFIX = "sqs_"


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    name: str
    consumer: Consumer | None  # None = auth disabled (local development only)

    def scopes_for(self, datasource: str) -> set[str]:
        if self.consumer is None:
            return set(ALL_SCOPES)
        return self.consumer.scopes_for(datasource)


ANONYMOUS = Principal(name="anonymous", consumer=None)


class Authenticator:
    def __init__(self, settings: Settings):
        self.enabled = settings.server.auth_enabled
        self._digests: list[tuple[str, Consumer]] = [
            (d.lower(), c) for c in settings.consumers for d in c.key_sha256 if d and d.strip()
        ]

    def authenticate(self, api_key: str | None) -> Principal:
        if not self.enabled:
            return ANONYMOUS
        if not api_key:
            raise AuthError("Missing API key. Send 'Authorization: Bearer <key>'.")
        digest = hash_key(api_key)
        match: Consumer | None = None
        for known, consumer in self._digests:  # compare all, constant time each
            if hmac.compare_digest(known, digest):
                match = consumer
        if match is None:
            raise AuthError("Invalid API key.")
        return Principal(name=match.name, consumer=match)

    @staticmethod
    def require(principal: Principal, datasource: str, scope: str) -> None:
        scopes = principal.scopes_for(datasource)
        if not scopes:
            raise DataSourceNotFound(f"Unknown datasource '{datasource}'")
        if scope not in scopes:
            raise PermissionDenied(f"Your key lacks the '{scope}' scope on '{datasource}'.")

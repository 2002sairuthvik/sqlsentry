"""Any server that speaks the OpenAI chat-completions protocol.

Covers Groq, Ollama (``http://localhost:11434/v1``), vLLM, LM Studio, OpenRouter,
Together, Fireworks, Azure-compatible gateways and OpenAI itself. Uses plain httpx so
there is no vendor SDK dependency.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from ..config import ProviderConfig
from ..errors import ConfigError, LLMError
from ..types import Usage
from .base import LLMProvider, LLMResult, Message, is_daily_limit

log = logging.getLogger(__name__)

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
_MAX_BACKOFF_S = 20.0


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, name: str, cfg: ProviderConfig):
        if not cfg.base_url:
            raise ConfigError(f"Provider '{name}': base_url is required for openai_compatible")
        if not cfg.model:
            raise ConfigError(f"Provider '{name}': model is required")
        self.name = name
        self.model = cfg.model
        self.cfg = cfg
        self._json_mode = cfg.json_mode
        headers = {"Content-Type": "application/json"}
        key = cfg.api_key()
        if cfg.api_key_env and not key:
            log.warning("Provider '%s': env var %s is not set", name, cfg.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.Client(base_url=cfg.base_url.rstrip("/"), headers=headers, timeout=cfg.timeout_s)

    def complete(self, system: str, messages: list[Message], json_schema: dict[str, Any]) -> LLMResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            **self.cfg.options,
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}

        data = self._post(body)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Provider '{self.name}' returned an unexpected response shape") from e
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=data.get("model", self.model),
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = self._client.post("/chat/completions", json=body)
            except httpx.HTTPError as e:
                if attempt < self.cfg.max_retries:
                    attempt += 1
                    time.sleep(min(2**attempt, _MAX_BACKOFF_S))
                    continue
                raise LLMError(f"Provider '{self.name}' is unreachable: {type(e).__name__}") from e

            if resp.status_code == 400 and "response_format" in body and "response_format" in resp.text:
                log.info("Provider '%s' rejected JSON mode; retrying without it", self.name)
                self._json_mode = False
                body = {k: v for k, v in body.items() if k != "response_format"}
                continue
            daily_cap = resp.status_code == 429 and is_daily_limit(_error_text(resp))
            if resp.status_code in _RETRYABLE and attempt < self.cfg.max_retries and not daily_cap:
                attempt += 1
                hint = retry_after_seconds(resp)
                time.sleep(min(hint, _MAX_BACKOFF_S) if hint else min(2**attempt, _MAX_BACKOFF_S))
                continue
            if resp.status_code >= 400:
                details: dict[str, Any] = {"status": resp.status_code}
                hint = retry_after_seconds(resp)
                if hint is not None:
                    details["retry_after_s"] = hint
                raise LLMError(
                    f"Provider '{self.name}' returned HTTP {resp.status_code}: {_error_text(resp)}",
                    details=details,
                )
            return resp.json()

    def close(self) -> None:
        self._client.close()


_TRY_AGAIN = re.compile(r"try again in\s+(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)(ms|s))?", re.IGNORECASE)


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """How long the server asked us to wait: the Retry-After header, or a hint in the error
    message such as Groq's "Please try again in 7.5s" / "in 1m2.5s" / "in 450ms"."""
    try:
        return max(float(resp.headers.get("retry-after", "")), 0.0)
    except ValueError:
        pass
    m = _TRY_AGAIN.search(_error_text(resp))
    if not m or not any(m.groups()):
        return None
    hours, minutes, amount, unit = m.groups()
    seconds = float(hours or 0) * 3600 + float(minutes or 0) * 60
    if amount:
        seconds += float(amount) / (1000 if unit.lower() == "ms" else 1)
    return seconds


def _error_text(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            return str(err.get("message", err))[:300]
        return str(err)[:300]
    except ValueError:
        return resp.text[:300]

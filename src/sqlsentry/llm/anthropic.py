"""Claude via the official ``anthropic`` SDK (``pip install sqlsentry[anthropic]``).

Uses structured outputs so the response is always schema-valid JSON, and server-side
refusal fallbacks (Claude API only; disable with ``options: {fallbacks: false}`` when
using a gateway or a cloud platform that doesn't support them).

Options: ``effort`` (low | medium | high | xhigh | max; default medium), ``fallbacks`` (bool).
"""

from __future__ import annotations

from typing import Any

from ..config import ProviderConfig
from ..errors import ConfigError, LLMError
from ..types import Usage
from .base import LLMProvider, LLMResult, Message

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    def __init__(self, name: str, cfg: ProviderConfig):
        try:
            import anthropic
        except ImportError as e:
            raise ConfigError("Provider type 'anthropic' needs: pip install 'sqlsentry[anthropic]'") from e
        self._anthropic = anthropic
        self.name = name
        self.model = cfg.model or DEFAULT_MODEL
        self.cfg = cfg
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / auth profiles itself.
        self._client = anthropic.Anthropic(
            api_key=cfg.api_key(),
            base_url=cfg.base_url or None,
            timeout=cfg.timeout_s,
            max_retries=cfg.max_retries,
        )

    def complete(self, system: str, messages: list[Message], json_schema: dict[str, Any]) -> LLMResult:
        a = self._anthropic
        opts = self.cfg.options
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.cfg.max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {
                "effort": opts.get("effort", "medium"),
                "format": {"type": "json_schema", "schema": json_schema},
            },
        }
        try:
            if opts.get("fallbacks", True):
                resp = self._client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = self._client.messages.create(**kwargs)
        except a.RateLimitError as e:
            raise LLMError(f"Provider '{self.name}' is rate limited", details={"status": 429}) from e
        except a.APIStatusError as e:
            raise LLMError(f"Provider '{self.name}' returned HTTP {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"Provider '{self.name}' is unreachable") from e

        if resp.stop_reason == "refusal":
            raise LLMError(f"Provider '{self.name}' declined this request")
        text = "".join(block.text for block in resp.content if block.type == "text")
        return LLMResult(
            text=text,
            model=resp.model,
            usage=Usage(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens),
        )

    def close(self) -> None:
        self._client.close()

"""Build providers from config. Custom providers: ``type: "my_pkg.module:MyProvider"``,
where ``MyProvider(name, cfg)`` implements :class:`~sqlsentry.llm.base.LLMProvider`."""

from __future__ import annotations

import importlib

from ..config import ProviderConfig
from ..errors import ConfigError
from .base import LLMProvider


def build_provider(name: str, cfg: ProviderConfig) -> LLMProvider:
    if cfg.type == "openai_compatible":
        from .openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(name, cfg)
    if cfg.type == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(name, cfg)
    if cfg.type == "fake":
        from .fake import FakeProvider

        return FakeProvider.from_config(name, cfg)
    if ":" in cfg.type:
        module_name, _, class_name = cfg.type.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError) as e:
            raise ConfigError(f"Provider '{name}': cannot load '{cfg.type}': {e}") from e
        provider = cls(name, cfg)
        if not isinstance(provider, LLMProvider):
            raise ConfigError(f"Provider '{name}': {cfg.type} is not an LLMProvider")
        return provider
    raise ConfigError(f"Provider '{name}': unknown type '{cfg.type}'")

"""A scripted provider for tests and offline demos. No network, fully deterministic."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from ..config import ProviderConfig
from ..errors import LLMError
from ..types import Usage
from .base import LLMProvider, LLMResult, Message

Scripted = str | dict[str, Any] | Callable[[str, list[Message]], str | dict[str, Any]]


class FakeProvider(LLMProvider):
    def __init__(self, responses: Iterable[Scripted] = (), *, name: str = "fake", model: str = "fake-model"):
        self.name = name
        self.model = model
        self.responses: deque[Scripted] = deque(responses)
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls, name: str, cfg: ProviderConfig) -> FakeProvider:
        return cls(cfg.options.get("responses", []), name=name, model=cfg.model or "fake-model")

    def push(self, *responses: Scripted) -> None:
        self.responses.extend(responses)

    def complete(self, system: str, messages: list[Message], json_schema: dict[str, Any]) -> LLMResult:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        if not self.responses:
            raise LLMError("FakeProvider has no scripted responses left")
        r = self.responses.popleft()
        if callable(r):
            r = r(system, messages)
        text = json.dumps(r) if isinstance(r, dict) else str(r)
        prompt_chars = len(system) + sum(len(m["content"]) for m in messages)
        return LLMResult(
            text=text,
            model=self.model,
            usage=Usage(input_tokens=prompt_chars // 4, output_tokens=len(text) // 4),
        )

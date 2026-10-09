"""The provider interface. The engine depends only on this, never on a vendor SDK."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..types import Usage

_DAILY_LIMIT = re.compile(r"per day|(TPD|RPD)|daily", re.IGNORECASE)


def is_daily_limit(message: str) -> bool:
    """True for per-day quota errors (e.g. Groq's 'tokens per day (TPD)'), where waiting a few
    seconds or minutes can't help, unlike per-minute rate limits."""
    return _DAILY_LIMIT.search(message or "") is not None


Message = dict[str, str]  # {"role": "user" | "assistant", "content": "..."}


@dataclass
class LLMResult:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)


class LLMProvider(ABC):
    """Turns a system prompt + messages into raw text that should contain the JSON answer.

    Providers may use native JSON / structured-output modes when available; the engine
    always validates the result itself, so a provider without them still works.
    """

    name: str
    model: str

    @abstractmethod
    def complete(self, system: str, messages: list[Message], json_schema: dict[str, Any]) -> LLMResult: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release network clients, if any."""

"""Storage interface for generations, executions and feedback (never result rows)."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from ..types import ExecutionRecord, Feedback, Generation


class Store(ABC):
    @abstractmethod
    def save_generation(self, gen: Generation) -> None: ...

    @abstractmethod
    def get_generation(self, generation_id: str) -> Generation | None: ...

    @abstractmethod
    def save_execution(self, record: ExecutionRecord) -> None: ...

    @abstractmethod
    def save_feedback(self, fb: Feedback) -> None: ...

    @abstractmethod
    def list_feedback(self, limit: int = 100) -> list[Feedback]: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        pass


class MemoryStore(Store):
    def __init__(self) -> None:
        self._gens: dict[str, Generation] = {}
        self._execs: list[ExecutionRecord] = []
        self._feedback: list[Feedback] = []
        self._lock = threading.Lock()

    def save_generation(self, gen: Generation) -> None:
        with self._lock:
            self._gens[gen.id] = gen

    def get_generation(self, generation_id: str) -> Generation | None:
        return self._gens.get(generation_id)

    def save_execution(self, record: ExecutionRecord) -> None:
        with self._lock:
            self._execs.append(record)

    def save_feedback(self, fb: Feedback) -> None:
        with self._lock:
            self._feedback.append(fb)

    def list_feedback(self, limit: int = 100) -> list[Feedback]:
        return list(reversed(self._feedback))[:limit]

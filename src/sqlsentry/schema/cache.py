"""A tiny thread-safe TTL cache. Swap for Redis when running multiple replicas."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_s: float):
        self.ttl_s = ttl_s
        self._items: dict[str, tuple[float, T]] = {}
        self._lock = threading.Lock()

    def get_or_load(self, key: str, loader: Callable[[], T]) -> T:
        with self._lock:
            hit = self._items.get(key)
            if hit and (self.ttl_s <= 0 or time.monotonic() - hit[0] < self.ttl_s):
                return hit[1]
        value = loader()
        with self._lock:
            self._items[key] = (time.monotonic(), value)
        return value

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._items.clear()
            else:
                self._items.pop(key, None)

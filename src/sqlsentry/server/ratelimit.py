"""In-memory token bucket per consumer. Good for a single instance or a demo; put a
gateway or Redis-backed limiter in front when running several replicas."""

from __future__ import annotations

import threading
import time

from ..errors import RateLimited


class RateLimiter:
    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.capacity <= 0:
            return
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1:
                retry = int((1 - tokens) / self.rate) + 1
                raise RateLimited(
                    f"Rate limit exceeded; retry in {retry}s.", details={"retry_after_s": retry}
                )
            self._buckets[key] = (tokens - 1, now)

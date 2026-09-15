"""Two independent throttles.

TokenBucket guards the *inbound* API — it rejects immediately so callers get a
fast 429 instead of queueing.

Pacer guards the *outbound* hits on LinkedIn. It is the one that protects the
egress IP: a single inbound request may escalate through several strategies, so
inbound budget alone would not bound our actual egress rate.
"""

from __future__ import annotations

import asyncio
import random
import time


class TokenBucket:
    def __init__(self, capacity: int, per_seconds: float = 60.0):
        self.capacity = max(1, capacity)
        self.per_seconds = per_seconds
        self._refill_rate = self.capacity / per_seconds
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _replenish(self, now: float) -> None:
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self._refill_rate)
            self._updated = now

    async def acquire(self) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        async with self._lock:
            now = time.monotonic()
            self._replenish(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True, 0.0
            deficit = 1.0 - self._tokens
            return False, deficit / self._refill_rate

    async def tokens_left(self) -> int:
        async with self._lock:
            self._replenish(time.monotonic())
            return int(self._tokens)


class Pacer:
    """Serialises outbound calls and enforces a jittered minimum gap."""

    def __init__(self, min_interval_s: float, jitter_s: float = 0.0):
        self.min_interval_s = max(0.0, min_interval_s)
        self.jitter_s = max(0.0, jitter_s)
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> float:
        """Block until the next outbound call is due. Returns seconds slept."""
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_allowed - now)
            gap = self.min_interval_s + random.uniform(0.0, self.jitter_s)
            self._next_allowed = max(now, self._next_allowed) + gap
        if delay:
            await asyncio.sleep(delay)
        return delay

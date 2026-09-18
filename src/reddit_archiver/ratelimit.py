"""Adaptive rate limiting.

Combines two mechanisms:
  * A minimum interval between requests (a floor), enforced across a bounded
    number of concurrent workers via a semaphore.
  * Dynamic backoff driven by the server's rate-limit headers
    (X-RateLimit-Remaining / X-RateLimit-Reset), which Arctic Shift and
    PullPush both expose. When remaining is low we sleep until reset.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Mapping

log = logging.getLogger(__name__)


class AdaptiveRateLimiter:
    def __init__(
        self,
        *,
        min_interval_seconds: float,
        max_workers: int,
        respect_headers: bool = True,
        low_remaining_threshold: int = 2,
    ) -> None:
        self.min_interval = min_interval_seconds
        self.respect_headers = respect_headers
        self.low_remaining = low_remaining_threshold
        self._sem = asyncio.Semaphore(max(1, max_workers))
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        # Absolute monotonic time until which all workers must pause (set when
        # the server signals we are (near) the limit).
        self._pause_until = 0.0

    async def acquire(self) -> None:
        """Block until a request may proceed, honoring interval + pause."""
        await self._sem.acquire()
        async with self._lock:
            now = time.monotonic()
            wait_until = max(self._next_allowed, self._pause_until)
            delay = wait_until - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval

    def release(self) -> None:
        self._sem.release()

    async def __aenter__(self) -> "AdaptiveRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        self.release()

    def update_from_headers(self, headers: Mapping[str, str]) -> None:
        """Inspect rate-limit headers and schedule a global pause if needed."""
        if not self.respect_headers:
            return
        remaining = _to_float(headers.get("x-ratelimit-remaining"))
        reset = _to_float(headers.get("x-ratelimit-reset"))
        if remaining is None:
            return
        if remaining <= self.low_remaining:
            # reset is typically "seconds until reset". Pause everyone.
            pause_for = reset if reset and reset > 0 else self.min_interval * 4
            self._pause_until = time.monotonic() + pause_for
            log.warning(
                "Rate limit low (remaining=%s); pausing ~%.1fs", remaining, pause_for
            )


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None

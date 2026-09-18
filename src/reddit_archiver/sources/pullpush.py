"""PullPush backend (FALLBACK).

https://api.pullpush.io  --  Pushshift-compatible schema. Slower and less
reliable than Arctic Shift (recurring outages), so it runs a single worker and
paces conservatively. Hard ceiling is 1000 req/hr; keep well under it.

Verified endpoints (pullpush.io):
  * GET /reddit/search/submission/ ?subreddit&after&before&size&sort&sort_type
  * GET /reddit/search/comment/    ?subreddit&after&before&size&sort&sort_type

Notes:
  * size: 1-100 (default 100). We sort ascending by created_utc and advance
    ``after`` to the last item's timestamp.
  * after/before accept unix seconds.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import BackendConfig
from ..ratelimit import AdaptiveRateLimiter
from ._http import get_json
from .base import Page, SourceBackend

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.pullpush.io"
USER_AGENT = "reddit-archiver/0.1 (+https://github.com/; archival research)"


class PullPushBackend(SourceBackend):
    name = "pullpush"

    def __init__(self, config: BackendConfig) -> None:
        self.base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        rl = config.rate_limit
        # PullPush size caps at 100; ignore "auto".
        self._size = 100 if rl.page_limit == "auto" else min(int(rl.page_limit), 100)
        self._max_retries = rl.max_retries
        self._backoff_base = rl.backoff_base_seconds
        self._backoff_max = rl.backoff_max_seconds
        self._limiter = AdaptiveRateLimiter(
            min_interval_seconds=rl.min_interval_seconds,
            max_workers=1,  # single worker regardless of config
            respect_headers=rl.respect_rate_limit_headers,
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    async def _search(self, path: str, subreddit: str, after: int, before: int,
                      cursor: int | None) -> Page:
        start = cursor if cursor is not None else after
        params: dict[str, Any] = {
            "subreddit": subreddit,
            "after": start,
            "before": before,
            "size": self._size,
            "sort": "asc",
            "sort_type": "created_utc",
        }
        payload = await get_json(
            self._client,
            f"{self.base_url}{path}",
            params,
            limiter=self._limiter,
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            backoff_max=self._backoff_max,
            backend_name=self.name,
        )
        items = _extract_items(payload)
        if not items:
            return Page(items=[], next_cursor=None, exhausted=True)
        last_epoch = _last_epoch(items)
        # A short page means the window is drained.
        exhausted = len(items) < self._size
        return Page(items=items, next_cursor=last_epoch, exhausted=exhausted)

    async def search_posts(self, subreddit: str, after: int, before: int,
                           cursor: int | None = None) -> Page:
        return await self._search(
            "/reddit/search/submission/", subreddit, after, before, cursor
        )

    async def search_comments(self, subreddit: str, after: int, before: int,
                              cursor: int | None = None) -> Page:
        return await self._search(
            "/reddit/search/comment/", subreddit, after, before, cursor
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    """PullPush returns {"data": [...]}."""
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, list) else []
    if isinstance(payload, list):
        return payload
    return []


def _last_epoch(items: list[dict[str, Any]]) -> int | None:
    for item in reversed(items):
        val = item.get("created_utc")
        if val is not None:
            try:
                return int(float(val))
            except (TypeError, ValueError):
                continue
    return None

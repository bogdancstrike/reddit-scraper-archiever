"""Arctic Shift backend (PRIMARY).

https://arctic-shift.photon-reddit.com  --  free, no auth, real date-range
search over Dec 2005 -> current month. This is the critical path: we page
through the whole window with a moving cursor and stream comments as a flat
list for local tree reconstruction.

Verified endpoints (api/README.md):
  * GET /api/posts/search    ?subreddit&after&before&limit&sort
  * GET /api/comments/search ?subreddit&after&before&limit&sort
  * GET /api/comments/tree   ?link_id&limit           (gap-fill fallback)

Notes:
  * limit: default 25, max 100, or "auto" (returns 100-1000). We sort ascending
    by created_utc and advance ``after`` to the last item's timestamp.
  * Rate limiting via X-RateLimit-Remaining / X-RateLimit-Reset headers.
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

DEFAULT_BASE_URL = "https://arctic-shift.photon-reddit.com"
USER_AGENT = "reddit-archiver/0.1 (+https://github.com/; archival research)"


class ArcticShiftBackend(SourceBackend):
    name = "arctic_shift"

    def __init__(self, config: BackendConfig) -> None:
        self.base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        rl = config.rate_limit
        self._page_limit = rl.page_limit
        self._max_retries = rl.max_retries
        self._backoff_base = rl.backoff_base_seconds
        self._backoff_max = rl.backoff_max_seconds
        self._limiter = AdaptiveRateLimiter(
            min_interval_seconds=rl.min_interval_seconds,
            max_workers=rl.max_workers,
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
            "limit": self._page_limit,
            "sort": "asc",  # ascending by created_utc -> forward-walking cursor
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
        return Page(items=items, next_cursor=last_epoch, exhausted=False)

    async def search_posts(self, subreddit: str, after: int, before: int,
                           cursor: int | None = None) -> Page:
        return await self._search("/api/posts/search", subreddit, after, before, cursor)

    async def search_comments(self, subreddit: str, after: int, before: int,
                              cursor: int | None = None) -> Page:
        return await self._search("/api/comments/search", subreddit, after, before, cursor)

    async def fetch_post_tree(self, post_id: str) -> list[dict[str, Any]]:
        link_id = post_id if post_id.startswith("t3_") else f"t3_{post_id}"
        payload = await get_json(
            self._client,
            f"{self.base_url}/api/comments/tree",
            {"link_id": link_id, "limit": 9999},
            limiter=self._limiter,
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            backoff_max=self._backoff_max,
            backend_name=self.name,
        )
        return _flatten_tree(_extract_items(payload))

    async def aclose(self) -> None:
        await self._client.aclose()


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    """Arctic Shift wraps results in {"data": [...]}; tolerate a bare list too."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        return []
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


def _flatten_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a nested comment-tree response into a list of comment dicts."""
    out: list[dict[str, Any]] = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        replies = node.pop("replies", None) or node.pop("children", None)
        if node.get("id") or node.get("body") is not None:
            out.append(node)
        if isinstance(replies, dict):
            replies = _extract_items(replies)
        if isinstance(replies, list):
            stack.extend(replies)
    return out

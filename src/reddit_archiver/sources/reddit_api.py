"""Reddit API backend (OPTIONAL ENRICHMENT) -- stub with a clean seam.

The official API caps listings at ~1000 items and has no date-range search, so
it is NOT a bulk-historical source. Its role is enrichment:
  (a) refresh current score/state on already-collected items, or
  (b) fetch a full live comment tree for specific posts where the archive looks
      incomplete.

This is intentionally a stub: the search_* methods raise, and the enrichment
entry points below define the seam. Wire up PRAW (honoring the ~100 queries/min
free-tier ceiling) when enrichment is needed. Requires the ``enrichment`` extra.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import RedditApiConfig
from .base import Page, SourceBackend

log = logging.getLogger(__name__)


class RedditApiBackend(SourceBackend):
    name = "reddit_api"

    def __init__(self, config: RedditApiConfig) -> None:
        self._config = config
        self._reddit = None  # lazily constructed PRAW client

    def _ensure_client(self):
        if self._reddit is not None:
            return self._reddit
        if not self._config.is_configured:
            raise RuntimeError(
                "reddit_api requires REDDIT_CLIENT_ID/SECRET/USER_AGENT to be set"
            )
        try:
            import praw  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "praw is not installed; install the 'enrichment' extra"
            ) from exc
        raise NotImplementedError(
            "reddit_api enrichment is a stub; wire up PRAW here when needed"
        )

    async def search_posts(self, subreddit: str, after: int, before: int,
                           cursor: int | None = None) -> Page:
        raise NotImplementedError(
            "reddit_api is enrichment-only (1000-item cap, no date search); "
            "do not use it as a bulk historical source"
        )

    async def search_comments(self, subreddit: str, after: int, before: int,
                              cursor: int | None = None) -> Page:
        raise NotImplementedError(
            "reddit_api is enrichment-only; use arctic_shift/pullpush for bulk"
        )

    # --- enrichment seam (phase-1 stubs) --------------------------------------
    async def refresh_scores(self, ids: list[str]) -> list[dict[str, Any]]:
        """(b) Refresh score/state for already-collected base-36 ids."""
        raise NotImplementedError("refresh_scores is a stub")

    async def fetch_post_tree(self, post_id: str) -> list[dict[str, Any]]:
        """(a) Fetch a full live comment tree for one post via PRAW."""
        raise NotImplementedError("reddit_api.fetch_post_tree is a stub")

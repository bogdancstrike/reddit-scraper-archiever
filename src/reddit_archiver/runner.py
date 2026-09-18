"""Orchestration: walk each subreddit's time window, persist, checkpoint.

Strategy (per the project design):
  * For each subreddit, page through posts and comments over [after, before)
    with a moving cursor (sort ascending by created_utc, advance ``after`` to
    the last item's timestamp). This gets past any per-request size cap.
  * Comments are streamed flat and stored with link_id/parent_id; the tree is
    materialized afterwards via a depth pass over the stored records.
  * Every page updates the scrape_progress checkpoint, so an interrupted run
    resumes exactly where it stopped.
  * On a page failure the primary backend falls back to the next in the chain.
  * SIGINT triggers a graceful stop: finish the current batch, checkpoint, exit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import AppConfig
from .filters import ContentFilter
from .models import NormalizedComment, normalize_comment, normalize_post
from .store import JsonProgressStore, JsonStorage
from .sources.base import BackendError, Page, SourceBackend
from .tree import build_forest, format_forest

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    posts: int = 0
    comments: int = 0
    started_at: float = field(default_factory=time.monotonic)


class Runner:
    def __init__(
        self,
        config: AppConfig,
        backends: list[SourceBackend],
        storage: JsonStorage,
        progress: JsonProgressStore,
        *,
        shutdown: asyncio.Event | None = None,
        max_pages: int | None = None,
        collect_comments: bool = False,
        content_filter: ContentFilter | None = None,
    ) -> None:
        self.config = config
        self.backends = backends
        self.storage = storage
        self.progress = progress
        self.shutdown = shutdown or asyncio.Event()
        self.max_pages = max_pages
        self.collect_comments = collect_comments
        self.content_filter = content_filter
        self.stats = RunStats()
        # dry-run comment sample for local tree reconstruction preview
        self._comment_sample: list[NormalizedComment] = []

    async def run(self) -> RunStats:
        after, before = self.config.date_range.resolve()
        log.info(
            "Window: %s -> %s (%d subreddit(s), backends=%s)",
            _fmt(after), _fmt(before), len(self.config.subreddits),
            [b.name for b in self.backends],
        )
        max_workers = self.config.backend_config(self.backends[0].name).rate_limit.max_workers
        sem = asyncio.Semaphore(max(1, max_workers))

        async def worker(sub: str) -> None:
            async with sem:
                await self._process_subreddit(sub, after, before)

        await asyncio.gather(*(worker(s) for s in self.config.subreddits))

        elapsed = time.monotonic() - self.stats.started_at
        log.info(
            "Done. posts=%d comments=%d in %.1fs",
            self.stats.posts, self.stats.comments, elapsed,
        )
        if self.collect_comments and self._comment_sample:
            roots = build_forest(self._comment_sample)
            log.info(
                "Reconstructed %d root comment(s) from %d sampled comment(s):\n%s",
                len(roots), len(self._comment_sample), format_forest(roots),
            )
        return self.stats

    async def _process_subreddit(self, subreddit: str, after: int, before: int) -> None:
        subreddit = subreddit.strip().lower()
        await self.storage.upsert_subreddit(subreddit)
        if self.config.fetch.posts and not self.shutdown.is_set():
            await self._walk(subreddit, after, before, kind="posts")
        if self.config.fetch.comments and not self.shutdown.is_set():
            await self._walk(subreddit, after, before, kind="comments")
            # Materialize the comment tree once the flat stream is stored.
            await self.storage.recompute_comment_depths(subreddit)

    async def _walk(self, subreddit: str, after: int, before: int, *, kind: str) -> None:
        primary = self.backends[0].name
        existing = await self.progress.get(subreddit, primary, kind)
        if existing and existing.status == "complete":
            log.info("[%s/%s] already complete; skipping", subreddit, kind)
            return

        cursor = existing.cursor_epoch if existing and existing.cursor_epoch else after
        total = existing.items_fetched if existing else 0
        log.info("[%s/%s] starting at %s (total so far=%d)", subreddit, kind, _fmt(cursor), total)

        prev_cursor: int | None = None
        pages = 0
        completed = False
        last_log = 0.0

        while not self.shutdown.is_set():
            if cursor >= before:
                completed = True
                break
            try:
                page = await self._fetch_page(kind, subreddit, after, before, cursor)
            except BackendError as exc:
                log.error("[%s/%s] all backends failed at %s: %s; will resume later",
                          subreddit, kind, _fmt(cursor), exc)
                break

            n = await self._persist(kind, subreddit, page)
            total += n
            pages += 1

            next_cursor = page.next_cursor
            await self.progress.upsert(
                subreddit=subreddit, backend=primary, kind=kind,
                window_start_epoch=after, window_end_epoch=before,
                cursor_epoch=next_cursor if next_cursor is not None else cursor,
                status="in_progress", items_fetched=total,
            )

            now = time.monotonic()
            if now - last_log > 15:
                _log_progress(subreddit, kind, cursor, after, before, total, self.stats)
                last_log = now

            if page.exhausted or next_cursor is None:
                completed = True
                break
            # Guard against a stuck cursor when a whole page shares one timestamp.
            if prev_cursor is not None and next_cursor <= cursor:
                next_cursor = cursor + 1
            prev_cursor = cursor
            cursor = next_cursor

            if self.max_pages is not None and pages >= self.max_pages:
                log.info("[%s/%s] reached max_pages=%d (dry-run cap)", subreddit, kind, self.max_pages)
                break

        status = "complete" if completed else "in_progress"
        await self.progress.upsert(
            subreddit=subreddit, backend=primary, kind=kind,
            window_start_epoch=after, window_end_epoch=before,
            cursor_epoch=min(cursor, before), status=status, items_fetched=total,
        )
        log.info("[%s/%s] %s (total=%d, pages=%d)", subreddit, kind, status, total, pages)

    async def _fetch_page(self, kind: str, subreddit: str, after: int, before: int,
                          cursor: int) -> Page:
        """Try each backend in order; return the first success."""
        last_err: Exception | None = None
        for backend in self.backends:
            try:
                if kind == "posts":
                    return await backend.search_posts(subreddit, after, before, cursor)
                return await backend.search_comments(subreddit, after, before, cursor)
            except BackendError as exc:
                last_err = exc
                log.warning("[%s/%s] backend %s failed: %s; trying fallback",
                            subreddit, kind, backend.name, exc)
            except NotImplementedError:
                continue  # e.g. reddit_api/dump stubs in the chain
        raise BackendError(f"no backend served {kind} for {subreddit}: {last_err}")

    async def _persist(self, kind: str, subreddit: str, page: Page) -> int:
        """Normalize a page, drop what the keyword filter rejects, and store it.

        Filtering here (rather than after the fact) keeps a filtered job's
        output to matching records only. The paging cursor comes from the raw
        page, so dropping records never disturbs the window walk.
        """
        if kind == "posts":
            posts = [p for r in page.items if (p := normalize_post(r, subreddit))]
            if self.content_filter:
                posts = [p for p in posts if self.content_filter.matches(p.title, p.selftext)]
            n = await self.storage.upsert_posts(posts)
            self.stats.posts += n
            return n
        comments = [c for r in page.items if (c := normalize_comment(r, subreddit))]
        if self.content_filter:
            comments = [c for c in comments if self.content_filter.matches(c.body)]
        n = await self.storage.upsert_comments(comments)
        self.stats.comments += n
        if self.collect_comments:
            room = 2000 - len(self._comment_sample)
            if room > 0:
                self._comment_sample.extend(comments[:room])
        return n


def _fmt(epoch: int | None) -> str:
    if epoch is None:
        return "?"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _log_progress(subreddit: str, kind: str, cursor: int, after: int, before: int,
                  total: int, stats: RunStats) -> None:
    span = max(1, before - after)
    frac = min(1.0, max(0.0, (cursor - after) / span))
    elapsed = time.monotonic() - stats.started_at
    eta = (elapsed * (1 - frac) / frac) if frac > 0.01 else float("nan")
    eta_str = f"{eta / 60:.1f}m" if eta == eta else "?"
    log.info(
        "[%s/%s] %s (%.1f%% of window) total=%d eta~%s",
        subreddit, kind, _fmt(cursor), frac * 100, total, eta_str,
    )

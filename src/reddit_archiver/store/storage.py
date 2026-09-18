"""Idempotent JSON writes for subreddits, posts, and comments.

Each subreddit gets ``<output>/<subreddit>/{subreddit,posts,comments}.json``.
Records are keyed on Reddit's base-36 ids so reruns don't duplicate and DO
refresh mutable fields (score, num_comments, deleted state, raw); ``fetched_at``
is preserved from the first insert while ``updated_at`` advances on every write.

Phase-2 fields (flagged/flag_reason/matched_terms) are deliberately NOT touched
here so the filter can own them.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import NormalizedComment, NormalizedPost
from . import _io

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class JsonStorage:
    """Per-subreddit JSON persistence. Async API mirrors the old DB Storage."""

    def __init__(self, output_dir: str | Path) -> None:
        self._dir = Path(output_dir)
        # One lock per subreddit guards its files (concurrent runners archive
        # different subreddits, so contention is effectively nil).
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock(self, subreddit: str) -> asyncio.Lock:
        return self._locks[_io.safe_subreddit(subreddit)]

    def _posts_path(self, subreddit: str) -> Path:
        return _io.subreddit_dir(self._dir, subreddit) / "posts.json"

    def _comments_path(self, subreddit: str) -> Path:
        return _io.subreddit_dir(self._dir, subreddit) / "comments.json"

    def _subreddit_path(self, subreddit: str) -> Path:
        return _io.subreddit_dir(self._dir, subreddit) / "subreddit.json"

    async def upsert_subreddit(self, name: str, raw: dict | None = None) -> None:
        name = name.strip().lower()
        async with self._lock(name):
            path = self._subreddit_path(name)
            existing = _io.load_json(path, {}) or {}
            record = {
                "name": name,
                "first_seen_at": existing.get("first_seen_at") or _now_iso(),
                "raw": raw if raw is not None else existing.get("raw"),
                "updated_at": _now_iso(),
            }
            _io.write_json_atomic(path, record)

    async def upsert_posts(self, posts: list[NormalizedPost]) -> int:
        if not posts:
            return 0
        subreddit = posts[0].subreddit
        async with self._lock(subreddit):
            path = self._posts_path(subreddit)
            store: dict[str, Any] = _io.load_json(path, {}) or {}
            for p in posts:
                store[p.id] = _merge(store.get(p.id), _post_to_dict(p))
            _io.write_json_atomic(path, store)
        return len(posts)

    async def upsert_comments(self, comments: list[NormalizedComment]) -> int:
        if not comments:
            return 0
        subreddit = comments[0].subreddit
        async with self._lock(subreddit):
            path = self._comments_path(subreddit)
            store: dict[str, Any] = _io.load_json(path, {}) or {}
            for c in comments:
                store[c.id] = _merge(store.get(c.id), _comment_to_dict(c))
            _io.write_json_atomic(path, store)
        return len(comments)

    async def recompute_comment_depths(self, subreddit: str) -> None:
        """Materialize the ``depth`` field for a subreddit's comments.

        Mirrors the old recursive-CTE pass: top-level comments (parent is the
        post, ``t3_``) are depth 0; each ``t1_`` hop adds one. Orphans whose
        parent isn't in the store are treated as roots so nothing is dropped.
        """
        subreddit = subreddit.strip().lower()
        async with self._lock(subreddit):
            path = self._comments_path(subreddit)
            store: dict[str, Any] = _io.load_json(path, {}) or {}
            if not store:
                return
            _assign_depths(store)
            _io.write_json_atomic(path, store)


def _merge(old: dict | None, new: dict) -> dict:
    """Refresh a record while preserving first-insert / phase-2 provenance."""
    if not old:
        return new
    merged = {**old, **new}
    merged["fetched_at"] = old.get("fetched_at", new["fetched_at"])
    # Preserve any phase-2 annotations the filter may have written.
    for key in ("flagged", "flag_reason", "matched_terms"):
        if key in old:
            merged[key] = old[key]
    # Depth is recomputed separately; keep a prior value until then.
    if "depth" in old and "depth" not in new:
        merged["depth"] = old["depth"]
    return merged


def _post_to_dict(p: NormalizedPost) -> dict[str, Any]:
    now = _now_iso()
    return {
        "id": p.id,
        "subreddit": p.subreddit,
        "author": p.author,
        "title": p.title,
        "selftext": p.selftext,
        "url": p.url,
        "permalink": p.permalink,
        "score": p.score,
        "num_comments": p.num_comments,
        "created_utc": _iso(p.created_utc),
        "created_epoch": p.created_epoch,
        "is_deleted": p.is_deleted,
        "over_18": p.over_18,
        "raw": p.raw,
        "fetched_at": now,
        "updated_at": now,
    }


def _comment_to_dict(c: NormalizedComment) -> dict[str, Any]:
    now = _now_iso()
    return {
        "id": c.id,
        "post_id": c.post_id,
        "parent_id": c.parent_id,
        "subreddit": c.subreddit,
        "author": c.author,
        "body": c.body,
        "score": c.score,
        "depth": None,
        "created_utc": _iso(c.created_utc),
        "created_epoch": c.created_epoch,
        "is_deleted": c.is_deleted,
        "raw": c.raw,
        "fetched_at": now,
        "updated_at": now,
    }


def _assign_depths(store: dict[str, dict]) -> None:
    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []
    for cid, rec in store.items():
        pid = rec.get("parent_id")
        if pid and pid.startswith("t1_") and pid[3:] in store:
            children[pid[3:]].append(cid)
        else:
            roots.append(cid)  # top-level (t3_) or orphan

    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque((r, 0) for r in roots)
    while queue:
        cid, depth = queue.popleft()
        if cid in seen:  # guard against pathological cycles
            continue
        seen.add(cid)
        store[cid]["depth"] = depth
        for child in children.get(cid, ()):
            queue.append((child, depth + 1))

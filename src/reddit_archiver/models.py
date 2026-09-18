"""Normalized record shapes shared by all source backends.

Arctic Shift and PullPush both return Reddit/Pushshift-schema records, so a
single normalizer handles both. Each backend hands raw dicts to these helpers,
which produce the flat shape the storage layer writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Author/body sentinels Reddit uses for removed content.
_DELETED_MARKERS = {"[deleted]", "[removed]", None, ""}


def _strip_prefix(thing_id: str | None) -> str | None:
    """Strip a t1_/t3_/t5_ type prefix, returning the base-36 id."""
    if not thing_id:
        return None
    if len(thing_id) > 3 and thing_id[2] == "_" and thing_id[0] == "t":
        return thing_id[3:]
    return thing_id


def _to_epoch(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_dt(epoch: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch is not None else None


def _clean_author(author: Any) -> str | None:
    if author in _DELETED_MARKERS:
        return None
    return str(author)


@dataclass(slots=True)
class NormalizedPost:
    id: str
    subreddit: str
    author: str | None
    title: str | None
    selftext: str | None
    url: str | None
    permalink: str | None
    score: int | None
    num_comments: int | None
    created_epoch: int | None
    created_utc: datetime | None
    is_deleted: bool
    over_18: bool | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(slots=True)
class NormalizedComment:
    id: str
    post_id: str
    parent_id: str | None
    subreddit: str
    author: str | None
    body: str | None
    score: int | None
    created_epoch: int | None
    created_utc: datetime | None
    is_deleted: bool
    raw: dict[str, Any] = field(repr=False)


def normalize_post(raw: dict[str, Any], subreddit: str) -> NormalizedPost | None:
    """Map a raw post record to NormalizedPost. Returns None if it has no id."""
    pid = _strip_prefix(raw.get("id") or raw.get("name"))
    if not pid:
        return None
    epoch = _to_epoch(raw.get("created_utc"))
    author = _clean_author(raw.get("author"))
    selftext = raw.get("selftext")
    is_deleted = author is None or selftext in ("[deleted]", "[removed]")
    return NormalizedPost(
        id=pid,
        subreddit=(raw.get("subreddit") or subreddit).strip().lower(),
        author=author,
        title=raw.get("title"),
        selftext=selftext,
        url=raw.get("url"),
        permalink=raw.get("permalink"),
        score=_coerce_int(raw.get("score")),
        num_comments=_coerce_int(raw.get("num_comments")),
        created_epoch=epoch,
        created_utc=_to_dt(epoch),
        is_deleted=is_deleted,
        over_18=raw.get("over_18"),
        raw=raw,
    )


def normalize_comment(raw: dict[str, Any], subreddit: str) -> NormalizedComment | None:
    """Map a raw comment record to NormalizedComment. Returns None without an id."""
    cid = _strip_prefix(raw.get("id") or raw.get("name"))
    if not cid:
        return None
    post_id = _strip_prefix(raw.get("link_id"))
    if not post_id:
        return None
    epoch = _to_epoch(raw.get("created_utc"))
    author = _clean_author(raw.get("author"))
    body = raw.get("body")
    is_deleted = author is None or body in ("[deleted]", "[removed]")
    return NormalizedComment(
        id=cid,
        post_id=post_id,
        parent_id=raw.get("parent_id"),  # keep prefixed for tree reconstruction
        subreddit=(raw.get("subreddit") or subreddit).strip().lower(),
        author=author,
        body=body,
        score=_coerce_int(raw.get("score")),
        created_epoch=epoch,
        created_utc=_to_dt(epoch),
        is_deleted=is_deleted,
        raw=raw,
    )


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

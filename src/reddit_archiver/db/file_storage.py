"""File-based storage — drop-in replacement for db.storage.Storage when
OUTPUT_TO_FILE is set.

Writes posts/comments as JSONL per subreddit instead of upserting into
Postgres, and reconstructs comment depth + a nested tree locally instead of
the SQL recursive pass ``recompute_comment_depths`` normally runs.

Layout under ``output_dir``::

    {subreddit}/posts.jsonl        one JSON object per line
    {subreddit}/comments.jsonl     one JSON object per line, gains "depth"
    {subreddit}/comment_tree.json  nested forest (for humans / quick inspection)

Writes append as pages arrive — cheap, and safe to Ctrl-C mid-run.
``recompute_comment_depths`` is the one finalize step: it de-duplicates by id
(last write wins, mirroring Postgres's ON CONFLICT DO UPDATE), computes each
comment's depth from its parent chain, and compacts both files in place.

Note: this compaction only runs for a subreddit if ``fetch.comments`` is
enabled (that's the only point Runner calls it). If you run with
``fetch.comments: false``, posts.jsonl for that subreddit won't be
de-duplicated — in practice this only matters if a run is interrupted and
resumed, and even then produces at most a handful of duplicate lines at the
resume boundary, not silent data loss.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from ..models import NormalizedComment, NormalizedPost

_POST_PREFIX = "t3_"
_COMMENT_PREFIX = "t1_"


def _to_jsonable(obj: Any) -> dict[str, Any]:
    d = asdict(obj) if is_dataclass(obj) else dict(obj)
    if d.get("created_utc") is not None:
        d["created_utc"] = d["created_utc"].isoformat()
    return d


class FileStorage:
    """Same public shape as db.storage.Storage: async upsert_subreddit(),
    upsert_posts(), upsert_comments(), recompute_comment_depths().
    """

    def __init__(self, output_dir: str | Path) -> None:
        self._root = Path(output_dir)

    def _dir(self, subreddit: str) -> Path:
        return self._root / subreddit

    async def upsert_subreddit(self, subreddit: str) -> None:
        await asyncio.to_thread(self._dir(subreddit).mkdir, parents=True, exist_ok=True)

    async def upsert_posts(self, posts: list[NormalizedPost]) -> int:
        if not posts:
            return 0
        path = self._dir(posts[0].subreddit) / "posts.jsonl"
        await asyncio.to_thread(self._append_jsonl, path, posts)
        return len(posts)

    async def upsert_comments(self, comments: list[NormalizedComment]) -> int:
        if not comments:
            return 0
        path = self._dir(comments[0].subreddit) / "comments.jsonl"
        await asyncio.to_thread(self._append_jsonl, path, comments)
        return len(comments)

    async def recompute_comment_depths(self, subreddit: str) -> None:
        await asyncio.to_thread(self._finalize_subreddit, subreddit)

    async def close(self) -> None:
        return None

    # -- blocking helpers, always run via asyncio.to_thread -----------------

    def _append_jsonl(self, path: Path, records: Iterable[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(_to_jsonable(r), default=str, ensure_ascii=False))
                f.write("\n")

    def _read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        out: list[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _write_jsonl(self, path: Path, records: Iterable[dict]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False))
                f.write("\n")
        tmp.replace(path)

    def _finalize_subreddit(self, subreddit: str) -> None:
        d = self._dir(subreddit)
        d.mkdir(parents=True, exist_ok=True)

        # --- posts: dedupe by id, last write wins ---
        posts = self._read_jsonl(d / "posts.jsonl")
        if posts:
            deduped = {p["id"]: p for p in posts}
            self._write_jsonl(d / "posts.jsonl", deduped.values())

        # --- comments: dedupe, compute depth, write nested tree ---
        comments = self._read_jsonl(d / "comments.jsonl")
        if not comments:
            return
        by_id = {c["id"]: c for c in comments}  # last write wins
        parent_of = {cid: c.get("parent_id") for cid, c in by_id.items()}
        depth_cache: dict[str, int] = {}

        def depth_of(cid: str, seen: set[str] | None = None) -> int:
            if cid in depth_cache:
                return depth_cache[cid]
            seen = seen or set()
            if cid in seen:
                depth_cache[cid] = 0  # cycle guard; shouldn't happen in practice
                return 0
            seen.add(cid)
            parent = parent_of.get(cid)
            if not parent or parent.startswith(_POST_PREFIX):
                depth_cache[cid] = 0
            elif parent.startswith(_COMMENT_PREFIX):
                parent_id = parent[len(_COMMENT_PREFIX):]
                if parent_id in by_id:
                    depth_cache[cid] = 1 + depth_of(parent_id, seen)
                else:
                    # Parent comment fell outside the fetched window (or was
                    # deleted upstream) — treat this comment as a root.
                    depth_cache[cid] = 0
            else:
                depth_cache[cid] = 0
            return depth_cache[cid]

        for cid, c in by_id.items():
            c["depth"] = depth_of(cid)
        self._write_jsonl(d / "comments.jsonl", by_id.values())

        # nested forest, for quick human inspection
        children: dict[str, list[dict]] = {}
        roots: list[dict] = []
        for c in by_id.values():
            parent = c.get("parent_id") or ""
            parent_id = parent[len(_COMMENT_PREFIX):] if parent.startswith(_COMMENT_PREFIX) else None
            if parent_id and parent_id in by_id:
                children.setdefault(parent_id, []).append(c)
            else:
                roots.append(c)

        def nest(c: dict) -> dict:
            kids = sorted(children.get(c["id"], []), key=lambda x: x.get("created_epoch") or 0)
            return {**c, "replies": [nest(k) for k in kids]}

        roots.sort(key=lambda x: x.get("created_epoch") or 0)
        forest = [nest(r) for r in roots]
        (d / "comment_tree.json").write_text(
            json.dumps(forest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

"""Resumable checkpointing via a per-subreddit ``progress.json`` file.

One entry per (backend, kind) inside ``<output>/<subreddit>/progress.json``.
``cursor_epoch`` is the created_utc of the last processed item; an interrupted
run resumes from there.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _io

log = logging.getLogger(__name__)


@dataclass
class Progress:
    subreddit: str
    backend: str
    kind: str
    window_start_epoch: int | None
    window_end_epoch: int | None
    cursor_epoch: int | None
    status: str
    items_fetched: int


class JsonProgressStore:
    def __init__(self, output_dir: str | Path) -> None:
        self._dir = Path(output_dir)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock(self, subreddit: str) -> asyncio.Lock:
        return self._locks[_io.safe_subreddit(subreddit)]

    def _path(self, subreddit: str) -> Path:
        return _io.subreddit_dir(self._dir, subreddit) / "progress.json"

    async def get(self, subreddit: str, backend: str, kind: str) -> Progress | None:
        subreddit = subreddit.strip().lower()
        async with self._lock(subreddit):
            store: dict[str, Any] = _io.load_json(self._path(subreddit), {}) or {}
        row = store.get(f"{backend}:{kind}")
        return Progress(**row) if row else None

    async def upsert(
        self,
        *,
        subreddit: str,
        backend: str,
        kind: str,
        window_start_epoch: int,
        window_end_epoch: int,
        cursor_epoch: int | None,
        status: str,
        items_fetched: int,
    ) -> None:
        subreddit = subreddit.strip().lower()
        async with self._lock(subreddit):
            path = self._path(subreddit)
            store: dict[str, Any] = _io.load_json(path, {}) or {}
            store[f"{backend}:{kind}"] = {
                "subreddit": subreddit,
                "backend": backend,
                "kind": kind,
                "window_start_epoch": window_start_epoch,
                "window_end_epoch": window_end_epoch,
                "cursor_epoch": cursor_epoch,
                "status": status,
                "items_fetched": items_fetched,
            }
            _io.write_json_atomic(path, store)

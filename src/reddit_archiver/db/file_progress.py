"""File-based checkpoint store — drop-in replacement for db.progress.ProgressStore
when OUTPUT_TO_FILE is set.

Persists the same (subreddit, backend, kind) cursor state that the
Postgres-backed ``scrape_progress`` table would, but to a single JSON file
instead of a database, so an interrupted file-mode run resumes exactly where
it stopped — same guarantee as the DB-backed path.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class ProgressRecord:
    """Shape Runner expects back from .get(): .status, .cursor_epoch, .items_fetched."""

    subreddit: str
    backend: str
    kind: str
    window_start_epoch: int | None = None
    window_end_epoch: int | None = None
    cursor_epoch: int | None = None
    status: str = "in_progress"
    items_fetched: int = 0


def _key(subreddit: str, backend: str, kind: str) -> str:
    return f"{subreddit}::{backend}::{kind}"


class FileProgressStore:
    """Same public shape as db.progress.ProgressStore: async get()/upsert().

    Backed by a single JSON file (default: ``<output_dir>/_progress.json``).
    Writes are atomic (tmp file + os.replace) so a crash mid-write can't
    corrupt the checkpoint.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data: dict[str, dict] = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    async def get(self, subreddit: str, backend: str, kind: str) -> Optional[ProgressRecord]:
        async with self._lock:
            raw = self._data.get(_key(subreddit, backend, kind))
            return ProgressRecord(**raw) if raw else None

    async def upsert(
        self,
        *,
        subreddit: str,
        backend: str,
        kind: str,
        window_start_epoch: int | None,
        window_end_epoch: int | None,
        cursor_epoch: int | None,
        status: str,
        items_fetched: int,
    ) -> None:
        record = ProgressRecord(
            subreddit=subreddit,
            backend=backend,
            kind=kind,
            window_start_epoch=window_start_epoch,
            window_end_epoch=window_end_epoch,
            cursor_epoch=cursor_epoch,
            status=status,
            items_fetched=items_fetched,
        )
        async with self._lock:
            self._data[_key(subreddit, backend, kind)] = asdict(record)
            await asyncio.to_thread(self._flush)

    def _flush(self) -> None:
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=self._path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    async def close(self) -> None:
        return None

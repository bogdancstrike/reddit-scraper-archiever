"""Flatten a job folder into the manager-facing ``results.json``.

The archive itself stays in the per-subreddit layout (id-keyed maps, full
``raw`` copies). The manager wants one list it can count and forward, so this
writes a flat, chronologically sorted array where every record carries a
``type`` of ``post`` or ``comment`` - the same shape the X scraper's reporting
script tallies. ``raw`` is dropped here to keep the payload small; it remains in
``<subreddit>/posts.json`` / ``comments.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..store import _io

log = logging.getLogger(__name__)

RESULTS_FILE_NAME = "results.json"

_POST_KEYS = (
    "id", "subreddit", "author", "title", "selftext", "url", "permalink",
    "score", "num_comments", "created_utc", "created_epoch", "is_deleted", "over_18",
)
_COMMENT_KEYS = (
    "id", "post_id", "parent_id", "subreddit", "author", "body", "score", "depth",
    "created_utc", "created_epoch", "is_deleted",
)


def export_results(results_folder: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Write ``results.json`` for a finished job folder; return (path, records)."""
    records: list[dict[str, Any]] = []
    for sub_dir in sorted(p for p in results_folder.iterdir() if p.is_dir()):
        records.extend(
            _rows(_io.load_json(sub_dir / "posts.json", {}) or {}, "post", _POST_KEYS)
        )
        records.extend(
            _rows(_io.load_json(sub_dir / "comments.json", {}) or {}, "comment", _COMMENT_KEYS)
        )

    records.sort(key=lambda r: (r.get("created_epoch") or 0, r.get("id") or ""))
    path = results_folder / RESULTS_FILE_NAME
    _io.write_json_atomic(path, records)
    log.info(
        "results exported: %d record(s) -> %s",
        len(records), path,
    )
    return path, records


def count_by_type(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"post": 0, "comment": 0}
    for record in records:
        kind = record.get("type")
        if kind in counts:
            counts[kind] += 1
    return counts


def _rows(store: dict[str, dict], kind: str, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for record in store.values():
        row: dict[str, Any] = {"type": kind}
        row.update({k: record.get(k) for k in keys})
        # Carry phase-2 annotations through when the filter has already run.
        for extra in ("flagged", "flag_reason", "matched_terms"):
            if extra in record:
                row[extra] = record[extra]
        rows.append(row)
    return rows

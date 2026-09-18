#!/usr/bin/env python3
"""Merge a scraped subreddit's posts.json + comments.json into one flat file.

The archive stores each subreddit as id-keyed maps split by record kind:

    data/<subreddit>/posts.json      {"<id>": {...}, ...}
    data/<subreddit>/comments.json   {"<id>": {...}, ...}

This writes, next to them, a single chronological array where every record
carries a ``type`` of ``post`` or ``comment``:

    data/<subreddit>/<subreddit>_merged.json

Usage:

    python3 merge_output.py                 # every subreddit under ./data
    python3 merge_output.py /srv/reddit     # a different output root
    python3 merge_output.py data/romania    # just one subreddit folder

Stdlib only, so it runs on the host without installing the package. The record
shape matches the manager-mode ``results.json`` export
(src/reddit_archiver/manager/results.py) - ``raw`` is dropped to keep the file
small; it stays in posts.json / comments.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

MERGED_SUFFIX = "_merged.json"

# Field order of each emitted record; anything missing is written as null.
POST_KEYS = (
    "id", "subreddit", "author", "title", "selftext", "url", "permalink",
    "score", "num_comments", "created_utc", "created_epoch", "is_deleted", "over_18",
)
COMMENT_KEYS = (
    "id", "post_id", "parent_id", "subreddit", "author", "body", "score", "depth",
    "created_utc", "created_epoch", "is_deleted",
)
# Phase-2 annotations are carried through only when `filter` has already run.
OPTIONAL_KEYS = ("flagged", "flag_reason", "matched_terms")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="merge_output.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path", nargs="?", default="data",
        help="output root to scan (default: data), or a single subreddit folder",
    )
    parser.add_argument(
        "--indent", type=int, default=2,
        help="JSON indentation; 0 writes one compact line (default: 2)",
    )
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    folders = find_subreddit_dirs(root)
    if not folders:
        print(f"error: no posts.json / comments.json found under {root}", file=sys.stderr)
        return 2

    failed = 0
    total = 0
    for folder in folders:
        try:
            path, counts = merge_folder(folder, indent=args.indent)
        except ValueError as exc:  # unreadable/!map JSON - report and keep going
            print(f"error: {folder}: {exc}", file=sys.stderr)
            failed += 1
            continue
        total += counts["post"] + counts["comment"]
        print(
            f"{folder.name}: {counts['post']} post(s) + {counts['comment']} comment(s) "
            f"-> {path}"
        )

    print(f"merged {total} record(s) from {len(folders) - failed} subreddit folder(s)")
    return 1 if failed else 0


def find_subreddit_dirs(root: Path) -> list[Path]:
    """Directories holding a posts.json / comments.json, ``root`` included.

    Recursive, so it also covers manager-mode output where each job gets its
    own folder (``reddit_<arg-id>_.../<subreddit>/``).
    """
    if _is_subreddit_dir(root):
        return [root]
    return sorted(
        d for d in root.rglob("*") if d.is_dir() and _is_subreddit_dir(d)
    )


def _is_subreddit_dir(path: Path) -> bool:
    return (path / "posts.json").is_file() or (path / "comments.json").is_file()


def merge_folder(folder: Path, *, indent: int = 2) -> tuple[Path, dict[str, int]]:
    """Write ``<folder>/<name>_merged.json``; return (path, per-type counts)."""
    records = [
        *_rows(_load_map(folder / "posts.json"), "post", POST_KEYS),
        *_rows(_load_map(folder / "comments.json"), "comment", COMMENT_KEYS),
    ]
    # Chronological, with the id as a tiebreaker so the order is stable across
    # runs (records sharing a created_utc are common).
    records.sort(key=lambda r: (r["created_epoch"] or 0, r["id"] or ""))

    path = folder / f"{folder.name}{MERGED_SUFFIX}"
    _write_json_atomic(path, records, indent=indent)
    counts = {"post": 0, "comment": 0}
    for record in records:
        counts[record["type"]] += 1
    return path, counts


def _load_map(path: Path) -> dict[str, dict[str, Any]]:
    """Read one id-keyed store file. Missing is empty; malformed is an error."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not an id-keyed object")
    return data


def _rows(store: dict[str, Any], kind: str, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for record in store.values():
        if not isinstance(record, dict):
            continue
        row: dict[str, Any] = {"type": kind}
        row.update({k: record.get(k) for k in keys})
        for extra in OPTIONAL_KEYS:
            if extra in record:
                row[extra] = record[extra]
        rows.append(row)
    return rows


def _write_json_atomic(path: Path, data: Any, *, indent: int) -> None:
    """Write via a temp file in the same directory, then replace.

    Same approach as the archiver's store: a crash mid-write can never leave a
    truncated merged file behind.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=indent or None),
        encoding="utf-8",
    )
    os.replace(tmp, path)


if __name__ == "__main__":
    raise SystemExit(main())

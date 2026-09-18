"""Shared filesystem helpers for the JSON store.

Provides subreddit-directory resolution and atomic JSON read/write. Writes go
to a temp file in the same directory and are ``os.replace``d into place, so a
crash mid-write never leaves a truncated file behind.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# Subreddit names are already lowercased/stripped upstream; this is a final
# guard so a hostile/odd name can never escape the output directory.
_SAFE_NAME = re.compile(r"[^a-z0-9_]+")


def safe_subreddit(name: str) -> str:
    return _SAFE_NAME.sub("_", name.strip().lower()) or "_"


def subreddit_dir(output_dir: Path, subreddit: str) -> Path:
    d = output_dir / safe_subreddit(subreddit)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)

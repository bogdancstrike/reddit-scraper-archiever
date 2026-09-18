"""Keyword/regex matching + Excel export over stored posts and comments.

This is the phase-2 feature. It is intentionally independent of the archiver:
it scans the JSON archive on disk, sets flagged / flag_reason / matched_terms
in-place on the stored records, and writes flagged rows to an Excel workbook.

Matching:
  * keywords  -> case-insensitive substring match
  * regexes   -> Python ``re`` patterns (case-insensitive)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Phase2Config
from ..store import _io

log = logging.getLogger(__name__)


@dataclass
class Matcher:
    keywords: list[str]
    patterns: list[re.Pattern]

    @classmethod
    def build(cls, cfg: Phase2Config) -> "Matcher":
        return cls(
            keywords=[k.lower() for k in cfg.keywords if k.strip()],
            patterns=[re.compile(p, re.IGNORECASE) for p in cfg.regexes if p.strip()],
        )

    def match(self, text: str | None) -> list[str]:
        if not text:
            return []
        low = text.lower()
        hits = [k for k in self.keywords if k in low]
        hits += [p.pattern for p in self.patterns if p.search(text)]
        return hits


def run_filter(output_dir: str | Path, cfg: Phase2Config) -> dict[str, int]:
    """Flag matching posts/comments in-place and export them. Returns counts."""
    matcher = Matcher.build(cfg)
    if not matcher.keywords and not matcher.patterns:
        log.warning("phase2: no keywords or regexes configured; nothing to do")
        return {"posts": 0, "comments": 0}

    output_dir = Path(output_dir)
    flagged_posts = 0
    flagged_comments = 0
    export_posts: list[dict] = []
    export_comments: list[dict] = []

    for sub_dir in _subreddit_dirs(output_dir):
        posts_path = sub_dir / "posts.json"
        comments_path = sub_dir / "comments.json"
        posts = _io.load_json(posts_path, {}) or {}
        comments = _io.load_json(comments_path, {}) or {}

        flagged_posts += _scan_and_flag(posts, text_keys=("title", "selftext"), matcher=matcher)
        flagged_comments += _scan_and_flag(comments, text_keys=("body",), matcher=matcher)

        if posts:
            _io.write_json_atomic(posts_path, posts)
        if comments:
            _io.write_json_atomic(comments_path, comments)

        export_posts.extend(_flagged_post_rows(posts))
        export_comments.extend(_flagged_comment_rows(comments, posts))

    export_posts.sort(key=lambda r: r.get("created_utc") or "")
    export_comments.sort(key=lambda r: r.get("created_utc") or "")

    _export_xlsx(cfg.output_path, export_posts, export_comments)
    log.info(
        "phase2: flagged %d post(s), %d comment(s) -> %s",
        flagged_posts, flagged_comments, cfg.output_path,
    )
    return {"posts": flagged_posts, "comments": flagged_comments}


def _subreddit_dirs(output_dir: Path) -> list[Path]:
    if not output_dir.is_dir():
        return []
    return sorted(p for p in output_dir.iterdir() if p.is_dir())


def _scan_and_flag(store: dict[str, dict], *, text_keys: tuple[str, ...], matcher: Matcher) -> int:
    flagged = 0
    for rec in store.values():
        text = " \n ".join(str(rec[k]) for k in text_keys if rec.get(k))
        terms = matcher.match(text)
        if terms:
            rec["flagged"] = True
            rec["flag_reason"] = "keyword-match"
            rec["matched_terms"] = sorted(set(terms))
            flagged += 1
    return flagged


def _flagged_post_rows(posts: dict[str, dict]) -> list[dict]:
    keys = ("id", "subreddit", "author", "title", "selftext", "score",
            "created_utc", "permalink", "matched_terms")
    return [{k: p.get(k) for k in keys} for p in posts.values() if p.get("flagged")]


def _flagged_comment_rows(comments: dict[str, dict], posts: dict[str, dict]) -> list[dict]:
    keys = ("id", "post_id", "subreddit", "author", "body", "score",
            "depth", "created_utc", "matched_terms")
    rows = []
    for c in comments.values():
        if not c.get("flagged"):
            continue
        row = {k: c.get(k) for k in keys}
        parent = posts.get(c.get("post_id"))
        row["post_title"] = parent.get("title") if parent else None
        rows.append(row)
    return rows


def _export_xlsx(path: str, posts: list[dict], comments: list[dict]) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openpyxl is required for export; install the 'phase2' extra") from exc

    wb = Workbook()
    _write_sheet(wb.active, "flagged_posts", posts)
    _write_sheet(wb.create_sheet("flagged_comments"), None, comments)
    wb.save(path)


def _write_sheet(ws, title: str | None, rows: list[dict]) -> None:
    if title:
        ws.title = title
    if not rows:
        ws.append(["(none)"])
        return
    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([_cell(row.get(h)) for h in headers])


def _cell(value):
    if isinstance(value, list):
        return ", ".join(map(str, value))
    if value is not None and not isinstance(value, (str, int, float, bool)):
        return str(value)
    return value

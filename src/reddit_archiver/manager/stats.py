"""Per-target statistics for the scraper manager.

The manager wants one report per *subreddit*, not one per job: a single
``scraping_arg`` usually lists several, and "how did this subreddit do" is the
useful question. Each report says how many records the subreddit produced and
how far back the archiver actually reached (the oldest item it collected).

Attribution is direct here — every exported record carries its own
``subreddit`` field, so unlike the browser scrapers there is no filename slug
to map back. Names are compared case-insensitively and with any ``r/`` prefix
stripped, since the payload accepts both forms.

Subreddits that produced nothing are reported too, with ``records: 0`` — a
subreddit that silently returns nothing is exactly what the manager needs to
see.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

TARGET_TYPE = "subreddit"

# ``created_utc`` is an ISO string in the export; ``created_epoch`` is the
# numeric original and is what the records are sorted by.
DATE_KEYS = ("created_utc", "created_epoch")


@dataclass(slots=True)
class TargetStats:
    """One subreddit's outcome: how much came out, and how far back."""

    target: str
    target_type: str
    records: int
    oldest_post_date: datetime | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "target_type": self.target_type,
            "records": self.records,
            "oldest_post_date": (
                self.oldest_post_date.isoformat() if self.oldest_post_date else None
            ),
        }


def build_target_stats(records: list[dict], args) -> list[TargetStats]:
    """Group ``records`` by subreddit and summarise each."""
    buckets: dict[str, list[dict]] = {sub: [] for sub in args.subreddits}
    lookup = {_normalise(sub): sub for sub in args.subreddits}

    for record in records:
        name = record.get("subreddit") or ""
        target = lookup.get(_normalise(name))
        if target is None:
            target = name or "unknown"
            buckets.setdefault(target, [])
        buckets[target].append(record)

    return [
        TargetStats(target, TARGET_TYPE, len(items), oldest_date(items))
        for target, items in buckets.items()
    ]


def oldest_date(records: list[dict]) -> datetime | None:
    """The earliest creation date across ``records``, or None if undated."""
    dates = [parsed for parsed in (_record_date(r) for r in records) if parsed]
    return min(dates) if dates else None


def _record_date(record: dict) -> datetime | None:
    for key in DATE_KEYS:
        parsed = parse_timestamp(record.get(key))
        if parsed:
            return parsed
    return None


def parse_timestamp(value: Any) -> datetime | None:
    """Best-effort parse; anything unrecognised yields None, never raises."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Drop the offset so mixed-awareness values stay comparable in min().
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _normalise(value: str) -> str:
    """Fold 'r/Romania', '/r/romania' and 'romania' together."""
    text = str(value or "").strip().lower().lstrip("/")
    return text[2:] if text.startswith("r/") else text

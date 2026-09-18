"""Parse + validate one ``scraping_arg`` payload from the scraper manager.

The payload shape is shared with the X/Twitter scraper so the manager can drive
both with one schema; the fields are interpreted for Reddit as follows:

    target        subreddit(s) to archive: a name, ``r/name``, a full reddit
                  URL, a comma-separated string, or a list of any of those.
                  ``subreddits`` is accepted as an alias.
    startDate     window start, ``DD.MM.YYYY`` (ISO-8601 also accepted).
    endDate       window end, inclusive (the whole day is covered).
    lastDays      relative window when no start/end is given (default 365).
    allWords      every term must appear in the record.
    anyWords      at least one term must appear.
    noneWords     no term may appear.
    exactPhrase   the phrase must appear verbatim.
    fetchPosts    archive posts (default: true).
    fetchComments archive comments (default: true).
    backends      ordered backend chain, primary first.
    backendSettings  per-backend base_url / rate-limit overrides.
    maxPages      stop each walk after N pages (smoke tests).
    language      accepted but ignored - see below.

Everything a config YAML can express about *what* to archive is therefore
available here, which is what lets the manager run the image without mounting a
config file at all. A mounted config still supplies the defaults these fields
override.

``language`` has no equivalent here: the archive APIs return no language field
for posts or comments, so there is nothing to filter on. It is logged and
dropped rather than silently changing the result set.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..exceptions import InvalidScrapingParameters

log = logging.getLogger(__name__)

# The manager sends DD.MM.YYYY (as for the X scraper); ISO is accepted too.
_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")

_SUBREDDIT_RE = re.compile(r"[A-Za-z0-9_]{2,21}")

KNOWN_BACKENDS = ("arctic_shift", "pullpush", "reddit_api", "dump")

# Recognized payload keys; anything else is reported as a probable typo.
_KNOWN_FIELDS = frozenset({
    "target", "subreddits", "startDate", "endDate", "lastDays",
    "allWords", "anyWords", "noneWords", "exactPhrase", "language",
    "fetchPosts", "fetchComments", "backends", "backendSettings", "maxPages",
})


@dataclass(slots=True)
class ScrapingArgs:
    subreddits: list[str]
    start_date: datetime | None = None
    end_date: datetime | None = None
    last_days: int | None = None
    all_words: list[str] = field(default_factory=list)
    any_words: list[str] = field(default_factory=list)
    none_words: list[str] = field(default_factory=list)
    exact_phrase: str | None = None
    language: str | None = None
    fetch_posts: bool = True
    fetch_comments: bool = True
    backends: list[str] = field(default_factory=list)
    backend_settings: dict[str, dict] = field(default_factory=dict)
    max_pages: int | None = None

    @property
    def has_keyword_filter(self) -> bool:
        return bool(self.all_words or self.any_words or self.none_words or self.exact_phrase)

    def window(self) -> tuple[datetime, datetime] | None:
        """Return the absolute (start, end) window, or None when unset.

        ``end_date`` is inclusive: a job for 01.01.2025 -> 31.01.2025 covers
        the whole of January.
        """
        if not (self.start_date and self.end_date):
            return None
        return self.start_date, self.end_date + timedelta(days=1) - timedelta(seconds=1)

    def describe(self) -> str:
        window = self.window()
        if window:
            period = f"{_fmt(window[0])} -> {_fmt(window[1])}"
        elif self.last_days:
            period = f"last {self.last_days} day(s)"
        else:
            period = "config default"
        return (
            f"subreddits={self.subreddits} window={period} "
            f"all_words={self.all_words} any_words={self.any_words} "
            f"none_words={self.none_words} exact_phrase={self.exact_phrase!r} "
            f"fetch_posts={self.fetch_posts} fetch_comments={self.fetch_comments} "
            f"backends={self.backends or 'config default'} "
            f"max_pages={self.max_pages or 'unlimited'}"
        )


def parse_scraping_args(
    raw: dict[str, Any],
    *,
    default_window: tuple[str | None, str | None] | None = None,
) -> ScrapingArgs:
    """Validate a raw ``scraping_arg`` dict into ScrapingArgs.

    ``default_window`` is the manager's ``start_date`` / ``end_date`` environment
    pair, used when the payload names no window of its own. A recurrent job has
    no window to name - the manager computes one per cycle, from the job's last
    run - and it arrives only in those variables. Without this fallback such a
    run silently reaches back ``lastDays`` (a year) on every cycle.

    Raises InvalidScrapingParameters for anything the archiver cannot act on.
    """
    if not isinstance(raw, dict):
        raise InvalidScrapingParameters("scraping_arg must be an object")

    unknown = sorted(set(raw) - _KNOWN_FIELDS)
    if unknown:
        log.warning("ignoring unrecognized scraping_arg field(s): %s", ", ".join(unknown))

    subreddits = _parse_targets(raw.get("target", raw.get("subreddits")))
    if not subreddits:
        raise InvalidScrapingParameters(
            "target is required and must name at least one subreddit "
            "(the archive APIs search by subreddit)"
        )

    start_date = _parse_date(raw.get("startDate"), "startDate")
    end_date = _parse_date(raw.get("endDate"), "endDate")
    if start_date is None and end_date is None and default_window:
        start_date = _parse_date(default_window[0], "start_date")
        end_date = _parse_date(default_window[1], "end_date")
        if start_date and end_date:
            log.info(
                "no startDate/endDate in the payload; using the manager's window "
                "%s -> %s", _fmt(start_date), _fmt(end_date),
            )
    if start_date and not end_date:
        raise InvalidScrapingParameters("endDate is required when startDate is set")
    if end_date and not start_date:
        raise InvalidScrapingParameters("startDate is required when endDate is set")
    if start_date and end_date and start_date > end_date:
        raise InvalidScrapingParameters(
            f"startDate ({_fmt(start_date)}) must not be after endDate ({_fmt(end_date)})"
        )

    last_days = _parse_positive_int(raw.get("lastDays"), "lastDays")
    if last_days and start_date:
        log.warning("both lastDays and startDate/endDate given; the explicit dates win")

    language = raw.get("language") or None
    if language:
        log.warning(
            "language=%r ignored: the archive APIs expose no language field to filter on",
            language,
        )

    return ScrapingArgs(
        subreddits=subreddits,
        start_date=start_date,
        end_date=end_date,
        last_days=last_days,
        all_words=_parse_words(raw.get("allWords")),
        any_words=_parse_words(raw.get("anyWords")),
        none_words=_parse_words(raw.get("noneWords")),
        exact_phrase=_parse_phrase(raw.get("exactPhrase")),
        language=language,
        fetch_posts=_parse_bool(raw.get("fetchPosts"), default=True),
        fetch_comments=_parse_bool(raw.get("fetchComments"), default=True),
        backends=_parse_backends(raw.get("backends")),
        backend_settings=_parse_backend_settings(raw.get("backendSettings")),
        max_pages=_parse_positive_int(raw.get("maxPages"), "maxPages"),
    )


def _parse_backends(value: Any) -> list[str]:
    """Validate the ordered backend chain, primary first."""
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    names: list[str] = []
    for item in items:
        name = str(item).strip()
        if not name:
            continue
        if name not in KNOWN_BACKENDS:
            raise InvalidScrapingParameters(
                f"unknown backend {name!r}; available: {', '.join(KNOWN_BACKENDS)}"
            )
        if name not in names:
            names.append(name)
    return names


def _parse_backend_settings(value: Any) -> dict[str, dict]:
    """Normalize per-backend overrides to the config models' snake_case keys.

    Both ``minIntervalSeconds`` and ``min_interval_seconds`` are accepted, so the
    payload can stay camelCase like the rest of the manager's schema.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidScrapingParameters("backendSettings must be an object keyed by backend name")

    settings: dict[str, dict] = {}
    for name, override in value.items():
        if name not in KNOWN_BACKENDS:
            raise InvalidScrapingParameters(
                f"unknown backend {name!r} in backendSettings; "
                f"available: {', '.join(KNOWN_BACKENDS)}"
            )
        if not isinstance(override, dict):
            raise InvalidScrapingParameters(f"backendSettings.{name} must be an object")
        settings[name] = _snake_keys(override)
    return settings


def _snake_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_snake(k): _snake_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snake_keys(v) for v in value]
    return value


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower()


def _parse_positive_int(value: Any, field_name: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InvalidScrapingParameters(
            f"{field_name}={value!r} must be a whole number"
        ) from None
    if number <= 0:
        raise InvalidScrapingParameters(f"{field_name}={value!r} must be greater than 0")
    return number


def _parse_targets(value: Any) -> list[str]:
    """Normalize targets to bare, lowercased subreddit names, order preserved."""
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]

    names: list[str] = []
    for item in raw_items:
        if item is None:
            continue
        if not isinstance(item, str):
            raise InvalidScrapingParameters(f"target entries must be strings, got {item!r}")
        for part in item.split(","):
            name = _subreddit_name(part)
            if name and name not in names:
                names.append(name)
    return names


def _subreddit_name(value: str) -> str | None:
    """Extract a subreddit name from a name, ``r/name``, or a reddit URL."""
    candidate = value.strip().rstrip("/")
    if not candidate:
        return None
    if "://" in candidate or candidate.lower().startswith("www."):
        match = re.search(r"/r/([A-Za-z0-9_]+)", candidate)
        if not match:
            raise InvalidScrapingParameters(f"target URL has no /r/<subreddit> path: {value!r}")
        candidate = match.group(1)
    else:
        candidate = re.sub(r"^/?r/", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.strip()
    if not _SUBREDDIT_RE.fullmatch(candidate):
        raise InvalidScrapingParameters(f"not a valid subreddit name: {value!r}")
    return candidate.lower()


def _parse_words(value: Any) -> list[str]:
    """Accept a list of terms or a whitespace-separated string."""
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split()
    words: list[str] = []
    for item in items:
        for word in str(item).split():
            if word and word not in words:
                words.append(word)
    return words


def _parse_phrase(value: Any) -> str | None:
    if value is None:
        return None
    phrase = " ".join(str(value).split())
    return phrase or None


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_date(value: Any, field_name: str) -> datetime | None:
    """Parse a manager date into midnight UTC. Blank/None means unset."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise InvalidScrapingParameters(
        f"{field_name}={value!r} is not in the expected format 'DD.MM.YYYY'"
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")

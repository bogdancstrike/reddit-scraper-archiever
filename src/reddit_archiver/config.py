"""Configuration models + loader (pydantic v2).

Loads YAML, expands ``${ENV_VAR}`` placeholders from the environment, and
validates into typed models. Secrets (Reddit creds) and deploy-specific paths
(the output directory) are expected to arrive via environment variables and are
never hardcoded.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} placeholders using os.environ.

    An unset variable expands to an empty string (so optional secrets simply
    become empty and downstream validation can decide what is required).
    """
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _prune_blanks(value: Any) -> Any:
    """Recursively drop keys/items whose value is an empty (or blank) string.

    An unset ``${VAR}`` expands to "" (see ``_expand_env``). Dropping those keys
    means "not configured here", so the field falls back to its default instead
    of failing validation. That is what lets a fully env-driven config -- e.g.
    the one baked into the image for Docker, where every value is a ``${VAR}``
    set from docker-compose -- leave any setting blank.
    """
    if isinstance(value, dict):
        pruned = {k: _prune_blanks(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if not _is_blank(v)}
    if isinstance(value, list):
        return [_prune_blanks(v) for v in value if not _is_blank(v)]
    return value


def _is_blank(value: Any) -> bool:
    return isinstance(value, str) and not value.strip()


def _split_list(value: Any, *, pattern: str = r"[,\n]") -> Any:
    """Accept a delimited string wherever a list of strings is expected.

    YAML cannot build a list out of a single ``${VAR}``, so env-driven configs
    pass ``SUBREDDITS=romania,cybersecurity``. Real YAML lists pass through
    untouched.
    """
    if isinstance(value, str):
        return [item.strip() for item in re.split(pattern, value) if item.strip()]
    return value


class RateLimitConfig(BaseModel):
    """Per-backend request pacing, concurrency, and retry/backoff params."""

    min_interval_seconds: float = 0.5
    max_workers: int = 3
    max_retries: int = 5
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 60.0
    respect_rate_limit_headers: bool = True
    # "auto" (Arctic Shift: 100-1000) or an int page size (<=100 for both APIs).
    page_limit: int | str = "auto"

    @field_validator("page_limit", mode="before")
    @classmethod
    def _numeric_page_limit(cls, value: Any) -> Any:
        """Env vars arrive as strings; keep "auto" but coerce "100" to an int."""
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return value


class BackendConfig(BaseModel):
    base_url: str | None = None
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


class DateRangeConfig(BaseModel):
    last_days: int | None = 365
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data: Any) -> Any:
        """Treat empty strings as unset.

        Unset ``${VAR}`` placeholders expand to "" (see ``_expand_env``); this
        lets a ``.env`` leave START_DATE/END_DATE/LAST_DAYS blank to fall back
        to the relative-window default instead of failing validation.
        """
        if isinstance(data, dict):
            return {
                k: (None if isinstance(v, str) and not v.strip() else v)
                for k, v in data.items()
            }
        return data

    def resolve(self) -> tuple[int, int]:
        """Return (after_epoch, before_epoch) as UTC unix seconds."""
        if self.start and self.end:
            start, end = self.start, self.end
        else:
            days = self.last_days if self.last_days is not None else 365
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
        start = _as_utc(start)
        end = _as_utc(end)
        if start >= end:
            raise ValueError(f"date_range start ({start}) must be before end ({end})")
        return int(start.timestamp()), int(end.timestamp())


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class FetchConfig(BaseModel):
    posts: bool = True
    comments: bool = True


class OutputConfig(BaseModel):
    """Where archived JSON is written. One directory per subreddit is created
    beneath ``dir`` (see the store package)."""

    dir: str = "data/output"

    def resolved_dir(self) -> Path:
        # An unset ${OUTPUT_DIR} expands to "" upstream; fall back to default.
        return Path(self.dir or "data/output")


class RedditApiConfig(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None
    user_agent: str | None = None
    queries_per_minute: int = 90

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.user_agent)


class Phase2Config(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    regexes: list[str] = Field(default_factory=list)
    output_path: str = "flagged.xlsx"

    @field_validator("keywords", mode="before")
    @classmethod
    def _split_keywords(cls, value: Any) -> Any:
        return _split_list(value)

    @field_validator("regexes", mode="before")
    @classmethod
    def _split_regexes(cls, value: Any) -> Any:
        # Newline-separated only: a regex may legitimately contain a comma.
        return _split_list(value, pattern=r"\n")


class DryRunConfig(BaseModel):
    last_days: int = 3
    max_pages: int = 2


class AppConfig(BaseModel):
    # Empty is invalid (see _check below); the default exists only so a config
    # whose `subreddits: "${SUBREDDITS}"` is unset fails with that message.
    subreddits: list[str] = Field(default_factory=list)
    date_range: DateRangeConfig = Field(default_factory=DateRangeConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    backends: list[str] = Field(default_factory=lambda: ["arctic_shift", "pullpush"])
    backend_settings: dict[str, BackendConfig] = Field(default_factory=dict)
    output: OutputConfig = Field(default_factory=OutputConfig)
    reddit_api: RedditApiConfig = Field(default_factory=RedditApiConfig)
    phase2: Phase2Config = Field(default_factory=Phase2Config)
    dry_run: DryRunConfig = Field(default_factory=DryRunConfig)
    log_level: str = "INFO"

    @field_validator("subreddits", "backends", mode="before")
    @classmethod
    def _split_names(cls, value: Any) -> Any:
        """Allow "romania,cybersecurity" (an env var) as well as a YAML list."""
        return _split_list(value)

    @model_validator(mode="after")
    def _check(self) -> "AppConfig":
        if not self.subreddits:
            raise ValueError(
                "config must list at least one subreddit "
                "(`subreddits:` in the YAML, or SUBREDDITS in the environment)"
            )
        if not self.backends:
            raise ValueError("config must list at least one backend")
        return self

    def backend_config(self, name: str) -> BackendConfig:
        return self.backend_settings.get(name, BackendConfig())


def load_config(path: str | Path) -> AppConfig:
    """Read a YAML config file, expand env vars, and validate."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return AppConfig.model_validate(_prune_blanks(_expand_env(raw)))

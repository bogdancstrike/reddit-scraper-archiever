"""The scraper-manager environment contract.

Mirrors the X/Twitter scraper: the manager injects ``id`` and ``scraping_args``
into the container and nothing else is required. Optional callback URLs enable
the HTTP reporting in ``client``; with them unset the run is file-output only.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from ..exceptions import ManagerEnvError

log = logging.getLogger(__name__)

REQUIRED_ENV_VARS = ("id", "scraping_args")


def getenv_required(key: str) -> str | None:
    return os.getenv(key)


@dataclass(slots=True)
class ScrapingEntry:
    """One ``scraping_args`` element: an id plus the job parameters."""

    scraping_arg_id: str
    scraping_arg: dict[str, Any]


@dataclass(slots=True)
class ManagerEnv:
    container_entity_id: str
    entries: list[ScrapingEntry]
    #: The manager's ``start_date`` / ``end_date`` (DD.MM.YYYY), when it sent
    #: them. This is the only place a recurrent job's per-cycle window appears -
    #: its ``scraping_arg`` carries no dates - so it is the fallback for entries
    #: that name no window of their own.
    window: tuple[str | None, str | None] = (None, None)


def load_manager_env() -> ManagerEnv:
    """Read + validate ``id`` and ``scraping_args`` from the environment.

    Raises ManagerEnvError when a required variable is missing or when
    ``scraping_args`` is not JSON of the expected shape.
    """
    missing = [key for key in REQUIRED_ENV_VARS if os.getenv(key) is None]
    if missing:
        raise ManagerEnvError(f"missing required env var(s): {', '.join(missing)}")

    container_entity_id = getenv_required("id") or ""
    raw = getenv_required("scraping_args") or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManagerEnvError(f"scraping_args is not valid JSON: {exc}") from exc

    entries = _as_entries(parsed)
    if not entries:
        raise ManagerEnvError("scraping_args contains no entries")

    window = (os.getenv("start_date") or None, os.getenv("end_date") or None)

    log.info(
        "Received ENV VARS: id=%s start_date=%s end_date=%s scraping_args=%s (%d entr%s)",
        container_entity_id, window[0], window[1], raw,
        len(entries), "y" if len(entries) == 1 else "ies",
    )
    return ManagerEnv(
        container_entity_id=container_entity_id, entries=entries, window=window,
    )


def _as_entries(parsed: Any) -> list[ScrapingEntry]:
    """Normalize the accepted ``scraping_args`` shapes into ScrapingEntry.

    Accepts the manager's list of ``{"scraping_arg_id", "scraping_arg"}``
    wrappers, a single such wrapper, or a bare parameters dict (handy when
    running the image by hand). A missing id falls back to the 1-based index.
    """
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ManagerEnvError(
            f"scraping_args must be a JSON object or list, got {type(parsed).__name__}"
        )

    entries: list[ScrapingEntry] = []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ManagerEnvError(f"scraping_args[{index - 1}] must be an object")
        args = item.get("scraping_arg")
        if args is None:  # bare parameters dict
            args = {k: v for k, v in item.items() if k != "scraping_arg_id"}
        if not isinstance(args, dict):
            raise ManagerEnvError(f"scraping_args[{index - 1}].scraping_arg must be an object")
        arg_id = item.get("scraping_arg_id")
        entries.append(
            ScrapingEntry(scraping_arg_id=str(arg_id) if arg_id is not None else str(index),
                          scraping_arg=args)
        )
    return entries

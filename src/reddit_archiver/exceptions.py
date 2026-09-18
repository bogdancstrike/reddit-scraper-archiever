"""Exceptions shared across the package."""

from __future__ import annotations


class InvalidScrapingParameters(ValueError):
    """A scraping_args entry from the scraper manager is unusable.

    Raised per entry, so one bad entry never aborts the whole batch.
    """


class ManagerEnvError(RuntimeError):
    """The scraper-manager environment contract (``id`` + ``scraping_args``)
    is missing or malformed. This is fatal for the whole container."""

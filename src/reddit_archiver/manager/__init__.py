"""Scraper-manager integration.

The manager launches this image as a one-shot container and passes the whole
job through two environment variables:

    id             the container entity id it tracks the run under
    scraping_args  a JSON list of {"scraping_arg_id": ..., "scraping_arg": {...}}

Each ``scraping_arg`` is one archiving job (subreddits + window + keyword
filters). They are processed in order; a bad or failing entry is logged and the
batch continues, and the container exits non-zero if any entry failed.

Results are written to disk per entry (see ``paths``/``results``) and, when the
manager callback URLs are configured, POSTed back to it (see ``client``).
"""

from .args import ScrapingArgs, parse_scraping_args
from .env import ScrapingEntry, load_manager_env
from .job import run_manager

__all__ = [
    "ScrapingArgs",
    "ScrapingEntry",
    "load_manager_env",
    "parse_scraping_args",
    "run_manager",
]

"""HTTP callbacks to the scraper manager.

Port of the X scraper's ``service/scraper_manager_client.py`` (same request
bodies and verbs), using httpx since that is already a dependency here.

Both callbacks are opt-in: with ``URL_SCRAPER_MANAGER_RESULTS`` /
``URL_SCRAPER_MANAGER_UPDATE_ARGS_STATUS`` unset the calls are skipped and the
run is file-output only, which is how the X scraper currently ships. Set the
URLs and the results POST / status PUT happen exactly as in that scraper.

A callback failure is logged and swallowed: the archive is already on disk, and
losing the manager ping must not fail an otherwise good job.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

# What kind of content this scraper reports; overridable if the manager's
# vocabulary differs per deployment (the X scraper reports "TWEETS").
SCRAPING_CONTENT = os.getenv("SCRAPING_CONTENT", "REDDIT_POSTS")

_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def results_url() -> str | None:
    return os.getenv("URL_SCRAPER_MANAGER_RESULTS") or None


def status_url() -> str | None:
    return os.getenv("URL_SCRAPER_MANAGER_UPDATE_ARGS_STATUS") or None


def stats_url() -> str | None:
    """Per-target stats endpoint.

    Falls back to the results URL with the path swapped, so a deployment that
    only sets URL_SCRAPER_MANAGER_RESULTS still reports stats.
    """
    explicit = os.getenv("URL_SCRAPER_MANAGER_STATS")
    if explicit:
        return explicit
    results = results_url()
    if results and results.endswith("/scraping_result"):
        return results[: -len("/scraping_result")] + "/scraping_stats"
    return None


def is_configured() -> bool:
    """True when at least one callback URL is set."""
    return bool(results_url() or status_url() or stats_url())


def generate_request_body_for_scraping_results(
    container_entity_id: str, source: str, posts: list[dict[str, Any]]
) -> dict[str, Any]:
    result = {
        "container_entity_id": container_entity_id,
        "scraping_content": SCRAPING_CONTENT,
        "source": source,
        "post_details": posts,
    }
    return {"source_id": container_entity_id, "result": result}


def send_results(results: dict[str, Any]) -> tuple[int | None, str]:
    url = results_url()
    if not url:
        log.debug("URL_SCRAPER_MANAGER_RESULTS not set; skipping results callback")
        return None, "not configured"
    return _send("POST", url, results, what="results")


STATUS_VALUES = {"success": "DONE", "error": "FAILED", "cancelled": "CANCELLED"}


def send_status_update(
    scraping_arg_id: Any,
    status: str,
    results_path: str | None = None,
    error: Exception | str | None = None,
    container_entity_id: str | None = None,
) -> tuple[int | None, str]:
    """Flip one scraping_arg to DONE or FAILED.

    Keyed on the **scraping_arg**, not the container: the manager tracks
    per-argument state (including the consecutive-failure count that eventually
    retires a bad target), and one container can carry several arguments.

    The endpoint requires `scraping_args_id` (int), `scraping_args_status`
    (a ScrapingArgsStatus value) and `scraping_results_path`. It answers 500 for
    anything else, which is what this used to send.
    """
    url = status_url()
    if not url:
        log.debug("URL_SCRAPER_MANAGER_UPDATE_ARGS_STATUS not set; skipping status callback")
        return None, "not configured"

    try:
        arg_id = int(scraping_arg_id)
    except (TypeError, ValueError):
        log.warning("Cannot report status: scraping_arg_id=%r is not an id", scraping_arg_id)
        return None, "no scraping_arg_id"

    body: dict[str, Any] = {
        "scraping_args_id": arg_id,
        "scraping_args_status": STATUS_VALUES.get(status, status),
        "scraping_results_path": results_path or "",
    }
    if container_entity_id is not None:
        body["container_entity_id"] = container_entity_id
    if error:
        body["error"] = str(error)
    return _send("PUT", url, body, what="status update")


def generate_request_body_for_scraping_stats(
    container_entity_id: str, scraping_arg_id: str | None, target_stats
) -> dict[str, Any]:
    """One report: what the target was, how much it yielded, how far back."""
    body: dict[str, Any] = {
        "container_entity_id": container_entity_id,
        "scraping_arg_id": scraping_arg_id,
        "platform": "REDDIT",
        "scraping_content": SCRAPING_CONTENT,
    }
    body.update(target_stats.as_payload())
    return body


def send_scraping_stats(stats: dict[str, Any]) -> tuple[int | None, str]:
    url = stats_url()
    if not url:
        log.debug("No stats URL configured; skipping per-target stats callback")
        return None, "not configured"
    return _send("POST", url, stats, what="scraping stats")


def _send(method: str, url: str, body: dict[str, Any], *, what: str) -> tuple[int | None, str]:
    try:
        log.info("Sending %s to %s", what, url)
        response = httpx.request(method, url, json=body, timeout=_TIMEOUT)
        if response.status_code == 200:
            log.info("%s sent successfully", what.capitalize())
        else:
            log.error("Failed to send %s: HTTP %s", what, response.status_code)
        return response.status_code, response.text
    except Exception as exc:  # network/DNS/timeout - never fatal for the job
        log.exception("Failed to send %s to scraper manager", what)
        return None, str(exc)

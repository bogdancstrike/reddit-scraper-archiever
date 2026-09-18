"""Shared async HTTP helper with tenacity retries + rate-limit awareness."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..ratelimit import AdaptiveRateLimiter
from .base import BackendError

log = logging.getLogger(__name__)

# Status codes worth retrying (transient): rate limit + server errors.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class RetryableHTTPError(Exception):
    """Transient HTTP failure that should be retried."""


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    *,
    limiter: AdaptiveRateLimiter,
    max_retries: int,
    backoff_base: float,
    backoff_max: float,
    backend_name: str,
) -> Any:
    """GET a JSON endpoint with pacing, backoff, and header-driven throttling.

    Raises BackendError if all retries are exhausted.
    """
    retryer = AsyncRetrying(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=backoff_base, max=backoff_max),
        retry=retry_if_exception_type((RetryableHTTPError, httpx.TransportError)),
        reraise=True,
    )
    try:
        async for attempt in retryer:
            with attempt:
                async with limiter:
                    resp = await client.get(url, params=params)
                limiter.update_from_headers(resp.headers)
                if resp.status_code in _RETRY_STATUS:
                    log.warning(
                        "%s %s -> %s (retrying)", backend_name, url, resp.status_code
                    )
                    raise RetryableHTTPError(f"status {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
    except (RetryableHTTPError, httpx.HTTPError) as exc:
        raise BackendError(f"{backend_name}: request to {url} failed: {exc}") from exc
    raise BackendError(f"{backend_name}: exhausted retries for {url}")

"""Dump backend (OPTIONAL, HEAVY) -- stub with a clean seam.

For very large subreddits where API paging is too slow, process Arctic Shift
monthly ``.zst`` dumps / HuggingFace Parquet offline. Use ``zstandard`` to
stream-decompress; do NOT require unpacking to disk. Requires the ``dumps``
extra.

Left as a stub on purpose: the seam is here (a local file/URL source instead of
HTTP paging) so it can be filled in later without touching the runner. The
runner treats any backend uniformly through the SourceBackend contract.
"""

from __future__ import annotations

import logging

from .base import Page, SourceBackend

log = logging.getLogger(__name__)


class DumpBackend(SourceBackend):
    name = "dump"

    def __init__(self, dump_dir: str | None = None) -> None:
        self._dump_dir = dump_dir

    async def search_posts(self, subreddit: str, after: int, before: int,
                           cursor: int | None = None) -> Page:
        raise NotImplementedError(
            "dump backend is a stub; stream .zst monthly dumps with zstandard here"
        )

    async def search_comments(self, subreddit: str, after: int, before: int,
                              cursor: int | None = None) -> Page:
        raise NotImplementedError(
            "dump backend is a stub; stream .zst monthly dumps with zstandard here"
        )

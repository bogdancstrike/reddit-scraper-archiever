"""Source-backend interface.

Every backend exposes the same small contract so the runner can walk a time
window without knowing which provider it talks to. A schema change in any one
provider stays isolated to that backend's module.

Contract (all times are UTC unix seconds):

    search_posts(subreddit, after, before, cursor)    -> Page
    search_comments(subreddit, after, before, cursor) -> Page
    fetch_post_tree(post_id)                           -> list[dict]  (optional)

``cursor`` is the resume point within [after, before): when set, the backend
starts there instead of at ``after``. A Page carries the raw records plus
``next_cursor`` (the created_utc of the last item, or None when the window is
exhausted). The runner advances the cursor and dedups on write.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Page:
    items: list[dict[str, Any]]
    next_cursor: int | None
    # True when the backend believes the window is fully drained.
    exhausted: bool = False


class BackendError(RuntimeError):
    """Raised when a backend cannot serve a page (after its own retries)."""


class SourceBackend(ABC):
    name: str = "base"

    @abstractmethod
    async def search_posts(
        self, subreddit: str, after: int, before: int, cursor: int | None = None
    ) -> Page: ...

    @abstractmethod
    async def search_comments(
        self, subreddit: str, after: int, before: int, cursor: int | None = None
    ) -> Page: ...

    async def fetch_post_tree(self, post_id: str) -> list[dict[str, Any]]:
        """Optional gap-fill: full comment tree for a single post."""
        raise NotImplementedError(f"{self.name} does not implement fetch_post_tree")

    async def aclose(self) -> None:  # pragma: no cover - trivial
        """Release network resources."""
        return None

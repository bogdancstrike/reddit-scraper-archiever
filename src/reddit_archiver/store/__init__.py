"""JSON file persistence layer.

Replaces the old PostgreSQL layer: each subreddit gets its own directory under
the configured output dir, holding ``posts.json``, ``comments.json``,
``subreddit.json`` and ``progress.json``. Writes are idempotent (keyed on
Reddit base-36 ids) and flushed atomically after every page so an interrupted
run resumes exactly where it stopped.
"""

from .progress import JsonProgressStore, Progress
from .storage import JsonStorage

__all__ = ["JsonProgressStore", "JsonStorage", "Progress"]

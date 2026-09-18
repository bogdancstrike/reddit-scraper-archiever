"""Configurable Reddit archiver -> JSON files.

Pulls all posts and full comment trees for a window of time from third-party
Reddit archives (Arctic Shift primary, PullPush fallback) into per-subreddit
JSON files, with resumable checkpointing for multi-day runs.
"""

__version__ = "0.1.0"

"""Build the ordered backend chain from config."""

from __future__ import annotations

from ..config import AppConfig
from .arctic_shift import ArcticShiftBackend
from .base import SourceBackend
from .dump import DumpBackend
from .pullpush import PullPushBackend
from .reddit_api import RedditApiBackend


def build_backend(name: str, config: AppConfig) -> SourceBackend:
    if name == "arctic_shift":
        return ArcticShiftBackend(config.backend_config(name))
    if name == "pullpush":
        return PullPushBackend(config.backend_config(name))
    if name == "reddit_api":
        return RedditApiBackend(config.reddit_api)
    if name == "dump":
        return DumpBackend()
    raise ValueError(f"unknown backend: {name!r}")


def build_backend_chain(config: AppConfig) -> list[SourceBackend]:
    """Instantiate backends in configured priority order (primary first)."""
    return [build_backend(name, config) for name in config.backends]

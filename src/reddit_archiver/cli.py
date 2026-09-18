"""Command-line interface.

    reddit-archiver run     --config config.yaml [--dry-run]
    reddit-archiver filter  --config config.yaml      (phase 2)
    reddit-archiver manager [--config config.yaml]    (scraper manager)

``manager`` is the entry point the scraper manager uses: the job comes from the
``id`` / ``scraping_args`` environment variables, so the config file is
optional there and only supplies defaults (backends, pacing, output root).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from .config import AppConfig, DateRangeConfig, load_config
from .logging_setup import setup_logging
from .runner import Runner
from .sources.factory import build_backend_chain
from .store import JsonProgressStore, JsonStorage

log = logging.getLogger("reddit_archiver")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reddit-archiver", description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="path to config YAML")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="archive the configured window")
    run_p.add_argument(
        "--dry-run", action="store_true",
        help="fetch one small slice from the first subreddit to validate end-to-end",
    )
    sub.add_parser("filter", help="phase 2: keyword filter + Excel export")
    sub.add_parser(
        "manager",
        help="scraper-manager mode: run the jobs in the `scraping_args` env var",
    )

    args = parser.parse_args(argv)
    _load_dotenv(args.config)

    if args.command == "manager":
        # The manager launches the image with env vars only; a mounted config
        # is welcome but not required.
        config = _load_optional_config(args.config)
        setup_logging(config.log_level if config else os.getenv("LOG_LEVEL", "INFO"))
        if config is None:
            log.info("no config at %s; using built-in defaults", args.config)
        return asyncio.run(_run_manager(config))

    config = load_config(args.config)
    setup_logging(config.log_level)

    if args.command == "run":
        return asyncio.run(_run(config, dry_run=args.dry_run))
    if args.command == "filter":
        from .phase2.keyword_filter import run_filter

        run_filter(config.output.resolved_dir(), config.phase2)
        return 0
    return 1


async def _run(config: AppConfig, *, dry_run: bool) -> int:
    if dry_run:
        config = _dry_run_config(config)
        log.info("DRY RUN: subreddit=%s, last_days=%s, max_pages=%s",
                 config.subreddits, config.date_range.last_days, config.dry_run.max_pages)

    output_dir = config.output.resolved_dir()
    log.info("Writing archive to %s/", output_dir)
    backends = build_backend_chain(config)
    shutdown = asyncio.Event()
    _install_signal_handlers(shutdown)

    try:
        runner = Runner(
            config,
            backends,
            JsonStorage(output_dir),
            JsonProgressStore(output_dir),
            shutdown=shutdown,
            max_pages=config.dry_run.max_pages if dry_run else None,
            collect_comments=dry_run,
        )
        await runner.run()
    finally:
        for backend in backends:
            await backend.aclose()
    return 0


async def _run_manager(config: AppConfig | None) -> int:
    from .manager import run_manager

    shutdown = asyncio.Event()
    _install_signal_handlers(shutdown)
    return await run_manager(config, _manager_output_root(config), shutdown=shutdown)


def _manager_output_root(config: AppConfig | None) -> Path:
    """Where per-job folders are created: config `output.dir`, else $OUTPUT_DIR."""
    if config is not None:
        return config.output.resolved_dir()
    return Path(os.getenv("OUTPUT_DIR") or "data/output")


def _load_optional_config(config_path: str) -> AppConfig | None:
    """Load the config if it exists; None when the caller relies on defaults."""
    if not Path(config_path).is_file():
        return None
    return load_config(config_path)


def _dry_run_config(config: AppConfig) -> AppConfig:
    """Narrow the config to a single small slice for end-to-end validation."""
    return config.model_copy(
        update={
            "subreddits": config.subreddits[:1],
            "date_range": DateRangeConfig(last_days=config.dry_run.last_days),
        }
    )


def _load_dotenv(config_path: str) -> None:
    """Populate os.environ from ``.env`` files (best effort).

    Looks in the current directory and next to the config file. Existing
    environment variables always win, so container-injected values and an
    explicit ``export`` override the file. Values may be quoted; ``export``
    prefixes and blank/``#`` lines are ignored.
    """
    seen: set[Path] = set()
    for candidate in (Path(".env"), Path(config_path).resolve().parent / ".env"):
        path = candidate.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            line = line.removeprefix("export ")
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _install_signal_handlers(shutdown: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _handler() -> None:
        if not shutdown.is_set():
            log.warning("shutdown requested; finishing current batch and checkpointing...")
            shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler)
        except NotImplementedError:  # pragma: no cover - e.g. Windows
            signal.signal(sig, lambda *_: _handler())


if __name__ == "__main__":
    raise SystemExit(main())

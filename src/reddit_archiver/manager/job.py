"""Run a scraper-manager batch: one archiving job per ``scraping_arg``.

Mirrors the X scraper's ``main()``: entries are processed in order, an invalid
or failing entry is logged and reported without aborting the rest, and the
process exits non-zero if any entry failed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ..config import AppConfig, BackendConfig, DateRangeConfig, FetchConfig, OutputConfig
from ..exceptions import InvalidScrapingParameters, ManagerEnvError
from ..filters import ContentFilter
from ..runner import Runner
from ..sources.factory import build_backend_chain
from ..store import JsonProgressStore, JsonStorage
from . import client
from .args import ScrapingArgs, parse_scraping_args
from .env import ScrapingEntry, load_manager_env
from .paths import generate_results_folder, mark_finished_scraping
from .results import count_by_type, export_results
from .stats import build_target_stats

log = logging.getLogger(__name__)


@dataclass(slots=True)
class JobResult:
    results_folder: Path
    results_file: Path
    posts: int
    comments: int
    # Kept so the per-target stats report can attribute records back to the
    # subreddit they came from.
    args: ScrapingArgs | None = None


async def run_manager(
    base_config: AppConfig | None,
    output_root: Path,
    *,
    shutdown: asyncio.Event | None = None,
) -> int:
    """Process every ``scraping_args`` entry. Returns the process exit code."""
    try:
        env = load_manager_env()
    except ManagerEnvError as exc:
        log.error("[fatal] scraper manager environment is unusable: %s", exc)
        return 1

    shutdown = shutdown or asyncio.Event()
    log.info("Writing manager output under %s/", output_root)
    had_failure = False

    for entry in env.entries:
        if shutdown.is_set():
            log.warning("shutdown requested; skipping remaining scraping_args entries")
            had_failure = True
            break
        try:
            result = await run_scraping_job(
                base_config, entry, output_root=output_root, shutdown=shutdown,
                default_window=env.window,
            )
            log.info(
                "Scraping finished for scraping_arg_id=%s: %d post(s), %d comment(s) in %s",
                entry.scraping_arg_id, result.posts, result.comments, result.results_folder,
            )
            _report_success(env.container_entity_id, entry, result)
        except InvalidScrapingParameters as exc:
            log.error("Invalid scraping_args for scraping_arg_id=%s: %s",
                      entry.scraping_arg_id, exc)
            client.send_status_update(entry.scraping_arg_id, "error", error=exc,
                                       container_entity_id=env.container_entity_id)
            had_failure = True
        except Exception as exc:
            log.exception("Scraping failed for scraping_arg_id=%s", entry.scraping_arg_id)
            client.send_status_update(entry.scraping_arg_id, "error", error=exc,
                                       container_entity_id=env.container_entity_id)
            had_failure = True

    return 1 if had_failure else 0


async def run_scraping_job(
    base_config: AppConfig | None,
    entry: ScrapingEntry,
    *,
    output_root: Path,
    shutdown: asyncio.Event | None = None,
    default_window: tuple[str | None, str | None] | None = None,
) -> JobResult:
    """Archive one ``scraping_arg`` into its own results folder."""
    args = parse_scraping_args(entry.scraping_arg, default_window=default_window)
    log.info("Starting scraping for scraping_arg_id=%s: %s", entry.scraping_arg_id, args.describe())

    results_folder = generate_results_folder(output_root, entry.scraping_arg_id, args)
    config = build_job_config(base_config, args, results_folder)
    content_filter = ContentFilter.build(
        args.all_words, args.any_words, args.none_words, args.exact_phrase,
    )
    if content_filter:
        log.info("Keyword filter active; only matching records are stored")

    backends = build_backend_chain(config)
    try:
        runner = Runner(
            config,
            backends,
            JsonStorage(results_folder),
            JsonProgressStore(results_folder),
            shutdown=shutdown,
            max_pages=args.max_pages,
            content_filter=content_filter,
        )
        stats = await runner.run()
    finally:
        for backend in backends:
            await backend.aclose()

    results_file, records = export_results(results_folder)
    counts = count_by_type(records)
    mark_finished_scraping(results_folder)
    return JobResult(
        results_folder=results_folder,
        results_file=results_file,
        posts=counts["post"] or stats.posts,
        comments=counts["comment"] or stats.comments,
        args=args,
    )


def build_job_config(
    base_config: AppConfig | None, args: ScrapingArgs, results_folder: Path
) -> AppConfig:
    """Overlay one job's parameters on the base config.

    Every field a config YAML uses to describe *what* to archive can come from
    the payload, so the container needs no mounted YAML when the manager drives
    it. A base config, when mounted, only supplies the defaults these override.
    """
    update = {
        "subreddits": args.subreddits,
        "fetch": FetchConfig(posts=args.fetch_posts, comments=args.fetch_comments),
        "output": OutputConfig(dir=str(results_folder)),
        "date_range": _date_range(base_config, args),
        "backend_settings": _backend_settings(base_config, args),
    }
    if args.backends:
        update["backends"] = args.backends
    # Left out otherwise, so the base config's chain - or AppConfig's default
    # ["arctic_shift", "pullpush"] - stands.
    if base_config is None:
        return AppConfig(**update)
    return base_config.model_copy(update=update)


def _date_range(base_config: AppConfig | None, args: ScrapingArgs) -> DateRangeConfig:
    """Absolute window wins, then lastDays, then the base config, then the default."""
    window = args.window()
    if window:
        return DateRangeConfig(last_days=None, start=window[0], end=window[1])
    if args.last_days:
        return DateRangeConfig(last_days=args.last_days)
    if base_config is not None:
        return base_config.date_range
    return DateRangeConfig()


def _backend_settings(
    base_config: AppConfig | None, args: ScrapingArgs
) -> dict[str, BackendConfig]:
    """Merge the payload's per-backend overrides over the base config's."""
    settings = dict(base_config.backend_settings) if base_config else {}
    for name, override in args.backend_settings.items():
        current = settings.get(name, BackendConfig())
        try:
            settings[name] = BackendConfig.model_validate(
                _deep_merge(current.model_dump(), override)
            )
        except ValidationError as exc:
            raise InvalidScrapingParameters(
                f"backendSettings.{name} is invalid: {exc}"
            ) from exc
    return settings


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _report_success(container_entity_id: str, entry: ScrapingEntry, result: JobResult) -> None:
    """POST the results and flip the arg to success (no-op without URLs)."""
    if not client.is_configured():
        return
    records = _load_records(result.results_file)
    body = client.generate_request_body_for_scraping_results(
        container_entity_id, _source_label(entry), records
    )
    client.send_results(body)
    _report_target_stats(container_entity_id, entry, result, records)
    client.send_status_update(entry.scraping_arg_id, "success",
                               results_path=str(result.results_folder),
                               container_entity_id=container_entity_id)


def _report_target_stats(
    container_entity_id: str,
    entry: ScrapingEntry,
    result: JobResult,
    records: list[dict],
) -> None:
    """One stats report per subreddit in this entry.

    Best-effort throughout: the archive is already on disk and already
    reported, so a bad date or an unreachable manager must not turn a good job
    into a failed one.
    """
    if result.args is None:
        return
    try:
        per_target = build_target_stats(records, result.args)
    except Exception:  # noqa: BLE001 - never fail a job over reporting
        log.exception("Could not compute per-target stats")
        return

    for target_stats in per_target:
        oldest = target_stats.oldest_post_date
        log.info(
            "subreddit=%s: %d record(s), oldest %s",
            target_stats.target, target_stats.records,
            oldest.isoformat() if oldest else "n/a",
        )
        client.send_scraping_stats(
            client.generate_request_body_for_scraping_stats(
                container_entity_id, entry.scraping_arg_id, target_stats
            )
        )


def _load_records(results_file: Path) -> list[dict]:
    from ..store import _io

    return _io.load_json(results_file, []) or []


def _source_label(entry: ScrapingEntry) -> str:
    target = entry.scraping_arg.get("target", entry.scraping_arg.get("subreddits"))
    if isinstance(target, list):
        return ", ".join(str(t) for t in target)
    return str(target) if target is not None else ""

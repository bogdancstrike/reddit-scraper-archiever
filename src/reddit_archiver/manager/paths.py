"""Per-job output folders and the completion flag file.

Each ``scraping_arg`` gets its own timestamped folder under the output root, so
concurrent jobs never share files and the manager can tell runs apart:

    <output>/reddit_<arg_id>_<label>_<start>_<end>_<ts>/
        <subreddit>/{subreddit,posts,comments,progress}.json
        results.json      flat, manager-facing export (see ``results``)
        gata.txt          written last: the folder is complete

Because the folder is new on every run, checkpoints do not carry over between
manager jobs - a re-run of the same arg archives the window from scratch.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..store import _io
from .args import ScrapingArgs

log = logging.getLogger(__name__)

# Same flag-file convention as the X scraper; blank disables the flag.
DONE_FILE_NAME_FLAG = os.getenv("DONE_FILE_NAME_FLAG", "gata.txt")


def generate_results_folder(output_root: Path, scraping_arg_id: str, args: ScrapingArgs) -> Path:
    """Create and return this job's output folder."""
    window = args.window()
    start_part = int(window[0].timestamp()) if window else "any"
    end_part = int(window[1].timestamp()) if window else "any"
    name = (
        f"reddit_{_io.safe_subreddit(scraping_arg_id)}_{_label(args)}"
        f"_{start_part}_{end_part}_{int(datetime.now(timezone.utc).timestamp())}"
    )
    folder = output_root / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def mark_finished_scraping(results_folder: Path) -> Path | None:
    """Touch the done flag so a watcher knows the folder is complete."""
    if not DONE_FILE_NAME_FLAG:
        return None
    flag = results_folder / DONE_FILE_NAME_FLAG
    flag.touch()
    log.info("done flag written: %s", flag)
    return flag


def _label(args: ScrapingArgs) -> str:
    if len(args.subreddits) == 1:
        return _io.safe_subreddit(args.subreddits[0])
    return f"{len(args.subreddits)}subs"

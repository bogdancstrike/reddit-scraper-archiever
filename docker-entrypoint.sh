#!/usr/bin/env bash
# Entry point for the reddit-archiver container.
#
#   * CONFIG_FILE (env) selects the YAML config. It defaults to the
#     env-driven /app/config.docker.yaml, whose every value is a ${VAR} set
#     from docker-compose (SUBREDDITS, LAST_DAYS, ...); point it at a mounted
#     file to use a hand-written config instead.
#   * OUTPUT_DIR (env) is expanded inside the config as ${OUTPUT_DIR}.
#   * MERGE_AFTER_RUN (env) chains merge_output.py onto a finished `run`, so
#     each subreddit folder in the mounted output dir also gets its flat
#     <subreddit>_merged.json. Set it to false to skip that step.
#   * Any args are passed straight through to the CLI. With none we pick the
#     subcommand from the environment: `manager` when the scraper manager
#     supplied `scraping_args`, otherwise `run`.
#
# Examples:
#   docker run ... reddit-archiver            -> run (full archive), then merge
#   docker run -e id=.. -e scraping_args=..   -> manager (scraper-manager job)
#   docker run ... reddit-archiver run --dry-run
#   docker run ... reddit-archiver filter     -> phase-2 keyword export
#   docker run ... reddit-archiver merge      -> merge an archive already on disk
set -euo pipefail

CONFIG_FILE="${CONFIG_FILE:-/app/config.docker.yaml}"
OUTPUT_DIR="${OUTPUT_DIR:-/data/output}"

# A bind-mounted volume is owned by the host (usually root), which masks the
# image's chown and leaves the unprivileged user unable to write. So when we
# start as root: create + take ownership of the output dir, then re-exec this
# same script as `scraper` (gosu execs in place, so tini/PID 1 still signals us).
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$OUTPUT_DIR"
  chown scraper:scraper "$OUTPUT_DIR" 2>/dev/null || true
  exec gosu scraper "$0" "$@"
fi

# Pick the subcommand when the caller passes no arguments: the scraper manager
# starts the container with `id` + `scraping_args` and nothing else.
if [ "$#" -eq 0 ]; then
  if [ -n "${scraping_args:-}" ]; then
    set -- manager
  else
    set -- run
  fi
fi

# `merge` is not a CLI subcommand: it runs the merge script over an archive
# that is already on disk. No config is involved.
if [ "$1" = "merge" ]; then
  shift
  if [ "$#" -eq 0 ]; then
    set -- "$OUTPUT_DIR"
  fi
  exec python3 /app/merge_output.py "$@"
fi

# `manager` takes the whole job from the environment, so a config file is
# optional there (it only supplies defaults). Every other command needs one.
if [ ! -f "$CONFIG_FILE" ] && [ "$1" != "manager" ]; then
  echo "ERROR: config not found at '$CONFIG_FILE'." >&2
  echo "Mount one in, e.g.  -v \"\$(pwd)/config.yaml:/app/config.yaml\"" >&2
  echo "or set CONFIG_FILE to a path inside the container." >&2
  exit 1
fi

# An archiving run normally ends by merging what it just wrote, so the host
# gets <subreddit>_merged.json next to posts.json / comments.json without a
# second command. `set -e` means a failed archive exits here and never merges.
# Only `run` chains: `filter` writes its own xlsx and `manager` its results.json.
merge_after_run="${MERGE_AFTER_RUN:-true}"
case "${merge_after_run,,}" in
  false|0|no|off) merge_after_run="" ;;
esac

if [ "$1" = "run" ] && [ -n "$merge_after_run" ]; then
  reddit-archiver --config "$CONFIG_FILE" "$@"
  echo "run finished; merging $OUTPUT_DIR"
  exec python3 /app/merge_output.py "$OUTPUT_DIR"
fi

exec reddit-archiver --config "$CONFIG_FILE" "$@"

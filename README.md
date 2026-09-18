# Reddit Archiver → JSON

Configurable tool that collects **all posts and full comment trees** for a
window of time (default: the last 365 days) from a list of subreddits and stores
everything as **JSON files on disk** (one directory per subreddit). Built for a
multi-day, resumable pull with minimal block risk.

> This tool uses **public third-party archive data** (Arctic Shift, PullPush).
> Please respect each service's rate limits and Reddit's Terms of Service.

## Why archives instead of scraping reddit.com

Reddit's own listings are hard-capped at ~1,000 items per listing and have no
date-range search, so a full year is impossible for active subreddits — and
direct scraping carries the highest block risk. Instead this tool uses
third-party archives that expose real date-range search:

| Backend        | Role        | Notes |
|----------------|-------------|-------|
| `arctic_shift` | **primary** | Free, no auth, Dec 2005 → current month, real date-range search. Comments are streamed flat and the tree is rebuilt locally from `link_id`/`parent_id`. |
| `pullpush`     | fallback    | Pushshift-compatible. Slower/less reliable; single worker, paced well under 1000 req/hr. |
| `reddit_api`   | enrichment (stub) | Official API via PRAW. Not for bulk history (1000-item cap) — only to refresh scores or fill gaps. |
| `dump`         | heavy (stub) | Offline `.zst` monthly dumps for very large subreddits. |

The key to finishing in days: **do not fetch comment trees post-by-post.**
Stream every comment in the window as a flat list and reconstruct each tree
locally (`depth` is computed from the parent chain over the stored records).

## Output layout

Everything is written under the configured output directory (`output.dir`,
default `./data/output`), **one directory per subreddit**:

```
data/output/
  <subreddit>/
    subreddit.json     metadata (name, first_seen_at, raw)
    posts.json         { "<base36-id>": { ...post... }, ... }
    comments.json      { "<base36-id>": { ...comment, "depth": N... }, ... }
    progress.json      resumable checkpoints, keyed "<backend>:<kind>"
```

Records are keyed on Reddit's base-36 ids and upserted, so reruns refresh
mutable fields (score, deleted state, `raw`, …) without duplicating. `fetched_at`
is preserved from the first insert; `updated_at` advances on each write. Each
record keeps a full `raw` copy of the original source record. Per-subreddit
files mean multiple containers writing to one shared output tree never collide.

## Architecture

```
src/reddit_archiver/
  config.py            pydantic config models + YAML loader (${ENV} expansion)
  models.py            normalized Post/Comment shapes + Reddit/Pushshift normalizers
  ratelimit.py         adaptive limiter (min-interval + X-RateLimit headers)
  tree.py              local comment-tree reconstruction / preview
  filters.py           keyword clauses (all/any/none/phrase) applied while archiving
  exceptions.py        InvalidScrapingParameters / ManagerEnvError
  runner.py            orchestration: window-walking, checkpointing, shutdown
  cli.py               run [--dry-run] | filter | manager   (+ .env autoload)
  manager/
    env.py             the `id` + `scraping_args` env contract
    args.py            one scraping_arg -> validated ScrapingArgs
    job.py             per-entry job loop, config overlay, exit code
    paths.py           per-job results folder + `gata.txt` done flag
    results.py         flat, typed `results.json` export
    client.py          results POST / status PUT callbacks (opt-in)
  sources/
    base.py            SourceBackend contract (search_posts/comments, fetch_post_tree)
    arctic_shift.py    PRIMARY backend
    pullpush.py        FALLBACK backend
    reddit_api.py      enrichment stub (PRAW seam)
    dump.py            heavy dump stub (zstandard seam)
    factory.py         builds the ordered backend chain from config
  store/
    storage.py         idempotent per-subreddit JSON upserts + depth pass
    progress.py        resumable per-(subreddit,backend,kind) checkpoints
    _io.py             atomic JSON read/write + safe path helpers
  phase2/
    keyword_filter.py  keyword/regex flagging + Excel export (reads the JSON archive)
```

A schema change in any one provider stays isolated to that backend's module —
they all satisfy the same small `SourceBackend` contract.

## Setup

Requires Python 3.11+. No database needed.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # core
# optional extras:
pip install -e ".[phase2]"      # Excel export (openpyxl)
pip install -e ".[enrichment]"  # reddit_api backend (praw)
pip install -e ".[dumps]"       # dump backend (zstandard)
pip install -e ".[dev]"         # tests

cp config.example.yaml config.yaml
cp .env.example .env            # set OUTPUT_DIR, time window, optional creds
$EDITOR config.yaml             # set subreddits, backends
$EDITOR .env                    # set the run window (LAST_DAYS or START/END)
```

### Where run params live

All scrape parameters live in the **config YAML** (`subreddits`, `backends`,
`fetch`, rate limits, phase-2 terms). Values that change per run or per
deployment can be pulled from the environment via `${VAR}` placeholders, and the
CLI auto-loads a `.env` file (from the current dir and next to the config) so
you don't have to `export` anything. Real environment variables always win over
`.env`.

`.env.example` documents the supported variables:

| Variable | Purpose |
|----------|---------|
| `OUTPUT_DIR` | Where JSON is written (empty → `./data/output`). |
| `LAST_DAYS` | Relative window: last N days (empty → 365). |
| `START_DATE` / `END_DATE` | Absolute window (ISO-8601). Set both for an exact period; wins over `LAST_DAYS`. |
| `REDDIT_CLIENT_ID` / `_SECRET` / `_USER_AGENT` | Only for the optional `reddit_api` backend. |

In **Docker** you don't write a config at all: the image ships one whose every
value is a `${VAR}`, and you set those variables in `docker-compose.yml` — see
[Docker](#docker).

**Run a specific period** — set both in `.env` and leave `LAST_DAYS` blank:

```dotenv
START_DATE=2024-01-01T00:00:00Z
END_DATE=2024-07-01T00:00:00Z
```

## Usage

```bash
# 1. Validate end-to-end on one small slice (recommended before a full run)
reddit-archiver --config config.yaml run --dry-run

# 2. Full run (resumable — safe to Ctrl-C and restart)
reddit-archiver --config config.yaml run

# 3. Phase 2: flag keyword/regex matches and export to Excel
reddit-archiver --config config.yaml filter

# 4. Scraper-manager mode: the job comes from env vars, config is optional
id=123 scraping_args='[...]' reddit-archiver manager
```

`--dry-run` fetches one small time-slice (see `dry_run` in the config) from the
first subreddit, writes it to JSON, and prints a reconstructed comment-tree
preview — a quick way to confirm the whole path before committing to a multi-day
pull.

### Resuming & robustness

- **Checkpointing**: a per-`(subreddit, backend, kind)` cursor is persisted in
  each subreddit's `progress.json` after every page. An interrupted run resumes
  exactly where it stopped; completed `(subreddit, kind)` pairs are skipped.
- **Retries/backoff**: `tenacity` retries 429/5xx/timeouts with exponential
  backoff and honors `X-RateLimit-Remaining` / `X-RateLimit-Reset`.
- **Fallback**: if the primary backend fails a page, the next backend in the
  configured chain is tried.
- **Graceful shutdown**: `SIGINT`/`SIGTERM` finishes the current batch,
  checkpoints, and exits.

## Docker

The image is **headless** — this archiver only talks to the Arctic Shift /
PullPush HTTP APIs and never opens a browser, so there is no Chrome/Xvfb/VNC
stack and no display ports to map. It's a small Python image that runs the CLI
and writes JSON to a mounted volume.

### Compose: configure once, `up -d`, results on the host

`docker-compose.yml` is the whole configuration surface. The image ships an
env-driven config (`config.docker.yaml`) in which every value is a `${VAR}`, so
the `environment:` block of a service *is* the config — no YAML to edit or
mount:

```yaml
services:
  scraper-a:
    environment:
      <<: *scrape-settings
      SUBREDDITS: "romania,casualromania"
```

The `x-scrape-settings` anchor at the top of the file holds the window,
backends, pacing and log level; a service merges it and sets its own
`SUBREDDITS`. The file itself is kept comment-free — the reference for every
variable is [DOCKER.md](DOCKER.md).

```bash
$EDITOR docker-compose.yml         # SUBREDDITS, LAST_DAYS / START_DATE+END_DATE, …
docker compose up -d --build       # build + start scraping in the background
docker compose logs -f             # follow progress
docker compose ps                  # a finished run shows as exited (0)
docker compose down                # stop; re-running resumes from the checkpoint
```

Results land **outside** the container, via the `./data` bind mount:

```
./data/<subreddit>/{subreddit,posts,comments,progress}.json
```

Point that somewhere else with `DATA_DIR=/srv/reddit` (shell or `.env`). Secrets
— the optional `REDDIT_*` credentials — go in `.env`, which every service loads;
values set in `docker-compose.yml` win over it.

When the run finishes it merges each subreddit into
`./data/<subreddit>/<subreddit>_merged.json` — one chronological array of posts
and comments (`MERGE_AFTER_RUN: "false"` turns that off; see
[docs/merge_scraper_outputs.md](docs/merge_scraper_outputs.md)).

Every setting available: `SUBREDDITS`, `LAST_DAYS`, `START_DATE`, `END_DATE`,
`FETCH_POSTS`, `FETCH_COMMENTS`, `BACKENDS`, the `ARCTIC_SHIFT_*` / `PULLPUSH_*`
pacing vars, `KEYWORDS` / `REGEXES` / `FLAGGED_OUTPUT` (phase 2),
`MERGE_AFTER_RUN`, `LOG_LEVEL`.
Lists are comma-separated (`SUBREDDITS=romania,cybersecurity`); anything left
blank falls back to its built-in default. See `config.docker.yaml` for the full
mapping.

One service, `scraper-a`, starts on `up`. To scrape more in parallel, copy the
block, give it a new name and its own `SUBREDDITS`; services share one `./data`
tree, and output is split per subreddit so they never write the same file.

```bash
docker compose up -d scraper-a      # start one service by name
docker compose logs -f scraper-a    # watch one service
```

### Single container, or a hand-written config

```bash
docker build -t reddit-archiver:latest .
docker run --rm \
  -e SUBREDDITS=romania -e LAST_DAYS=30 \
  -v "$(pwd)/data:/data/output" \
  reddit-archiver:latest run        # or: run --dry-run | filter

# …or bypass the env-driven config entirely with your own YAML:
docker run --rm \
  -e CONFIG_FILE=/app/config.yaml \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -v "$(pwd)/data:/data/output" \
  reddit-archiver:latest run
```

## Scraper manager integration

The scraper manager runs this image as a one-shot container and passes the whole
job in two environment variables — the same contract as the X/Twitter scraper,
so one manager schema drives both:

| Variable | Required | Meaning |
|----------|----------|---------|
| `id` | yes | Container entity id the manager tracks the run under. |
| `scraping_args` | yes | JSON list of `{"scraping_arg_id": …, "scraping_arg": {…}}` — one archiving job each. |
| `URL_SCRAPER_MANAGER_RESULTS` | no | Set it and results are `POST`ed back per entry. Unset → file output only. |
| `URL_SCRAPER_MANAGER_UPDATE_ARGS_STATUS` | no | Set it and a `success`/`error` status is `PUT` per entry. |
| `URL_SCRAPER_MANAGER_STATS` | no | Per-target stats (one call per subreddit): record count and oldest date. Unset → derived from `URL_SCRAPER_MANAGER_RESULTS`. |
| `SCRAPING_CONTENT` | no | Value reported in the results body (default `REDDIT_POSTS`). |

```bash
docker run --rm \
  -e id=123 \
  -e scraping_args='[{"scraping_arg_id": "1", "scraping_arg": {
        "target": ["romania", "r/cybersecurity"],
        "startDate": "01.01.2025", "endDate": "31.01.2025",
        "allWords": ["securitate"], "noneWords": ["curs"],
        "anyWords": ["ransomware", "malware"],
        "exactPhrase": "atac cibernetic", "fetchComments": true}}]' \
  -v "$(pwd)/data:/data/output" \
  reddit-archiver:latest
```

No `config.yaml` and no subcommand are needed: with `scraping_args` present the
entrypoint selects `manager` mode, and every job parameter — subreddits, what to
fetch, the period, the backend chain and its pacing — comes from the payload
(see the field table below). A config file may still be mounted, in which case
it only supplies defaults for whatever the payload leaves out.

### `scraping_arg` fields

A `scraping_arg` carries **everything a config YAML expresses about what to
archive**, which is why manager mode needs no config file:

| Field | Reddit meaning | YAML equivalent |
|-------|----------------|-----------------|
| `target` | Subreddit(s): a name, `r/name`, a reddit URL, a comma-separated string, or a list. **Required** — the archive APIs search by subreddit. `subreddits` is an accepted alias. | `subreddits` |
| `startDate` / `endDate` | Window in `DD.MM.YYYY` (ISO also accepted). Both or neither; `endDate` is inclusive of the whole day. | `date_range.start` / `.end` |
| `lastDays` | Relative window when no start/end is given (default 365). Explicit dates win. | `date_range.last_days` |
| `fetchPosts` / `fetchComments` | What to archive (both default `true`). | `fetch.posts` / `.comments` |
| `backends` | Ordered chain, primary first: `arctic_shift`, `pullpush`, `reddit_api`, `dump`. | `backends` |
| `backendSettings` | Per-backend `baseUrl` and `rateLimit` overrides (`minIntervalSeconds`, `maxWorkers`, `maxRetries`, `backoffBaseSeconds`, `backoffMaxSeconds`, `respectRateLimitHeaders`, `pageLimit`). camelCase or snake_case; merged over the defaults, so you only name what you change. | `backend_settings` |
| `maxPages` | Stop each subreddit/kind walk after N pages — handy for a smoke test. | `dry_run.max_pages` |
| `allWords` / `anyWords` / `noneWords` | Terms that must all / must at least one / must never appear. Whole-word, case-insensitive. | *(no YAML equivalent)* |
| `exactPhrase` | Phrase that must appear verbatim. | *(no YAML equivalent)* |
| `language` | Accepted but **ignored** — the archive APIs expose no language field to filter on; a warning is logged. | — |

Unrecognized fields are ignored with a warning, so a typo is visible in the log
rather than silently changing the run. The remaining YAML settings are
deployment-level, not job-level, and stay on the environment: `output.dir` →
`OUTPUT_DIR`, `log_level` → `LOG_LEVEL`, `reddit_api` credentials → `REDDIT_*`.

Replacing a config YAML (2 subreddits, both kinds, 30-day window, both
backends, gentler PullPush pacing) with a payload:

```json
[{"scraping_arg_id": "1", "scraping_arg": {
  "subreddits": ["Romania", "programming"],
  "lastDays": 30,
  "fetchPosts": true, "fetchComments": true,
  "backends": ["arctic_shift", "pullpush"],
  "backendSettings": {"pullpush": {"rateLimit": {"minIntervalSeconds": 6.0}}}
}}]
```

Keyword clauses are ANDed and applied *before* writing, so a filtered job's
output contains matching records only. Posts match on title + selftext,
comments on their body — a comment can match while its parent post does not, so
`results.json` may contain comments whose post is absent.

### Output per entry

Each `scraping_arg` gets its own timestamped folder under the output root:

```
data/output/reddit_<arg-id>_<label>_<start>_<end>_<ts>/
  <subreddit>/{subreddit,posts,comments,progress}.json   full archive (with `raw`)
  results.json     flat array, each record typed "post" | "comment", no `raw`
  gata.txt         written last — the folder is complete
```

`results.json` is what gets POSTed to the manager (as `result.post_details`).
Because the folder is new on each run, checkpoints do not carry across manager
jobs: re-running the same entry re-archives its window.

### Failure handling

Entries are processed in order. An invalid or failing entry is logged, reported
as `error`, and the batch continues; the container exits `1` if any entry
failed, `0` when all succeeded. A missing/malformed `id` or `scraping_args` is
fatal before any work starts. Callback failures never fail a job whose data is
already on disk.

## Configuration

See [`config.example.yaml`](config.example.yaml) for a fully commented example:
`subreddits`, `date_range` (relative `last_days` or absolute `start`/`end`, both
env-driveable), `backends` (ordered primary + fallbacks), per-backend
`rate_limits`/`concurrency`, `output.dir`, optional `reddit_api`, and the
phase-2 `keywords`/`regexes`.

## Phase 2: keyword filter

`reddit-archiver filter` scans the JSON archive on disk, sets
`flagged` / `flag_reason` / `matched_terms` in-place on matching records, and
exports flagged rows (with post context for comments) to an `.xlsx` workbook. It
never re-scrapes.

## Tests

```bash
pytest        # tree reconstruction, normalization, config, JSON store, backend paging
```

Tests need neither network nor a database. A live pull requires outbound access
to the archive hosts only.

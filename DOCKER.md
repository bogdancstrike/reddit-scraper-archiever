# Running the archiver with Docker

A practical guide to running the archiver container. This tool is **headless**
(a pure HTTP-API scraper — no browser/GUI), so the image is a slim Python image
and there are **no VNC/noVNC or display ports** to map. `docker compose up -d`
starts one scraper (`scraper-a`); you scale by adding more services, each with
its own subreddit set.

`docker-compose.yml` is deliberately comment-free — this file is the reference
for what every variable in it does.

## Quick answers

- **Configuration → `docker-compose.yml`, nothing else.** The image ships
  `config.docker.yaml`, whose every value is a `${VAR}`, so a service's
  `environment:` block *is* the config. No YAML to edit, no config to mount.
- **Output location → host `./data/`.** Compose maps `./data` (host) →
  `/data/output` (container), so results land outside the container at
  **`./data/<subreddit>/posts.json`** (etc.). Any additional container shares
  this one tree — per-subreddit folders keep them from colliding. Put the data
  somewhere else with `DATA_DIR=/srv/reddit` (in `.env` or the shell).
- **Date format → ISO-8601 UTC**, e.g. `2026-07-20T00:00:00Z` (the `Z` = UTC).
  For an exact period set **both** `START_DATE` and `END_DATE` and **leave
  `LAST_DAYS` blank**.
- **`.env` is for secrets only** (`REDDIT_*`) plus `DATA_DIR`. Every service
  loads it, and anything set in `docker-compose.yml` wins over it.

## Start scraping

```bash
$EDITOR docker-compose.yml     # SUBREDDITS per service, window, pacing
docker compose up -d --build   # build + start scraping in the background
docker compose logs -f         # follow progress
docker compose ps              # a finished run shows as exited (0)
docker compose down            # stop; re-running resumes from the checkpoint
```

Runs are **resumable** — stop any time and re-run; each container skips
already-`complete` subreddits and continues the rest from its checkpoint
(`progress.json`). `docker compose down` sends `SIGTERM`, which finishes the
current batch and checkpoints before exiting (`stop_grace_period: 60s`).

## The settings

All of these are environment variables in `docker-compose.yml`. Lists are
comma-separated. Blank (or removed) means "use the built-in default".

| Variable | Default | Meaning |
|----------|---------|---------|
| `SUBREDDITS` | *(required)* | Subreddits for this container, e.g. `romania,cybersecurity` (no `r/`). |
| `LAST_DAYS` | `365` | Relative window: last N days up to now. |
| `START_DATE` / `END_DATE` | unset | Absolute window (ISO-8601). Set **both**; wins over `LAST_DAYS`. |
| `FETCH_POSTS` / `FETCH_COMMENTS` | `true` | Which record kinds to fetch. |
| `BACKENDS` | `arctic_shift,pullpush` | Ordered chain; the rest are fallbacks when the primary fails a page. |
| `ARCTIC_SHIFT_MIN_INTERVAL` | `0.5` | Seconds between requests. |
| `ARCTIC_SHIFT_MAX_WORKERS` | `3` | Concurrency across subreddits. |
| `ARCTIC_SHIFT_MAX_RETRIES` / `_BACKOFF_BASE` / `_BACKOFF_MAX` | `5` / `2.0` / `60.0` | Retry behavior. |
| `ARCTIC_SHIFT_PAGE_LIMIT` | `auto` | `auto` (100–1000) or an int ≤ 100. |
| `PULLPUSH_*` | `4.0` / `1` / `100` | Same knobs for the PullPush backend. |
| `KEYWORDS` / `REGEXES` / `FLAGGED_OUTPUT` | — | Phase 2 (see below). `REGEXES` is newline-separated. |
| `MERGE_AFTER_RUN` | `true` | Merge each subreddit into `<subreddit>_merged.json` when the run finishes (see below). `false` skips it. |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. |
| `OUTPUT_DIR` | `/data/output` | Container path of the mount — leave it alone; change `DATA_DIR` instead. |
| `DATA_DIR` | `./data` | **Host** side of the mount (compose-level, from `.env` or the shell). |

`config.docker.yaml` shows how each one maps onto the config schema;
`config.example.yaml` documents every option the schema has.

## Adding a second container (the pattern)

To scrape two subreddit sets in parallel, copy the `scraper-a` block, rename it,
give it its own subreddits:

```yaml
  scraper-b:
    <<: *archiver-base
    container_name: reddit-archiver-b
    environment:
      <<: *scrape-settings
      SUBREDDITS: "learnpython,Python"
      LAST_DAYS: "30"
```

`LAST_DAYS` there is optional — a service can override anything the
`x-scrape-settings` anchor sets. Then:

```bash
docker compose up -d               # both
docker compose up -d scraper-b     # or one by name
docker compose logs -f scraper-b   # watch one container
```

## Validate before a long pull

`--dry-run` fetches one small slice (3 days, 2 pages) from the first subreddit,
writes it to JSON, and prints a reconstructed comment-tree preview:

```bash
docker compose run --rm scraper-a run --dry-run
```

## Merged output (automatic)

A finished `run` ends by merging what it just wrote, so each subreddit folder on
the host gets a flat, chronological file next to the archive:

```
./data/<subreddit>/<subreddit>_merged.json
```

You see it at the end of the log:

```
run finished; merging /data/output
romania: 20678 post(s) + 13557 comment(s) -> /data/output/romania/romania_merged.json
```

It is one array of posts and comments in `created_utc` order, each record
tagged `"type": "post"` or `"type": "comment"` — the shape most downstream
tools want. Full field reference: [docs/merge_scraper_outputs.md](docs/merge_scraper_outputs.md).

Set `MERGE_AFTER_RUN: "false"` in `docker-compose.yml` to skip the step. To
merge an archive that is already on disk (after changing your mind, or after
running phase 2 so the flags are carried through):

```bash
docker compose run --rm scraper-a merge
```

A failed archive never merges — the run's exit code stands and the step is
skipped. A merge that fails on a corrupt `posts.json` reports the folder and
exits non-zero, even though the archive itself is fine.

## Phase-2 keyword export (optional, after collecting)

The `filter` service runs the same image with a different command, reading the
JSON you already collected (it does **not** re-scrape). Set `KEYWORDS` /
`REGEXES` on it in `docker-compose.yml`, then:

```bash
docker compose run --rm filter
# -> ./data/flagged.xlsx on the host (FLAGGED_OUTPUT)
```

## Prefer a hand-written YAML?

Mount it over the image's config and point `CONFIG_FILE` at it; the environment
variables above are then unused:

```yaml
    environment:
      CONFIG_FILE: /app/config.yaml
      OUTPUT_DIR: /data/output
    volumes:
      - ${DATA_DIR:-./data}:/data/output
      - ./configs/scraper-a.yaml:/app/config.yaml:ro
```

## Single container (without compose)

```bash
docker build -t reddit-archiver:latest .
docker run --rm \
  -e SUBREDDITS=romania -e LAST_DAYS=30 \
  -v "$(pwd)/data:/data/output" \
  reddit-archiver:latest run        # or: run --dry-run | filter
```

## Volume permissions (handled automatically)

A bind-mounted `./data` is created **root-owned** by the Docker daemon, which
would otherwise stop the unprivileged in-container user from writing to it. The
container handles this itself: it starts as root, takes ownership of the output
dir, then drops to the `scraper` user (via `gosu`) before running. As a result
your host `./data/<subreddit>/…` files end up owned by uid `1000`. Nothing to do
on your side.

---

### Reference: what a real 2-day run produced

`SUBREDDITS=learnpython` with `START_DATE=2026-07-20T00:00:00Z` /
`END_DATE=2026-07-22T00:00:00Z` collected **47 posts / 401 comments**, wrote them
under `./data/learnpython/`, reconstructed the comment tree (depths 0–6, no
orphans), and marked both `posts` and `comments` progress `complete`. Re-running
skipped both (resumable + idempotent).

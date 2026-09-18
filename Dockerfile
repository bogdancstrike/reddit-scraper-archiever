FROM python:3.11-slim-bookworm

# ---- System deps ----
# This archiver is HEADLESS: it pulls from the Arctic Shift / PullPush JSON
# HTTP APIs and never opens a browser, so there is NO Chrome / Xvfb / VNC /
# noVNC stack here (unlike a Selenium-based scraper). Only TLS roots and an
# init that forwards signals (so SIGTERM triggers the graceful, checkpointing
# shutdown instead of being swallowed by PID 1).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
        gosu \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash scraper

WORKDIR /app
COPY . .

# ---- Python deps (core + phase-2 Excel export) ----
RUN pip install --no-cache-dir -e ".[phase2]"

# ---- Data dir + permissions ----
# JSON output lands here; mount a volume over it to persist across runs and to
# share one output tree between multiple containers.
RUN mkdir -p /data/output \
    && chown -R scraper:scraper /app /data \
    && chmod +x /app/docker-entrypoint.sh

# ---- Environment ----
# CONFIG_FILE points at the env-driven config shipped in the image: every one
# of its values is a ${VAR}, so a run is configured purely from the compose
# `environment:` block (SUBREDDITS, LAST_DAYS, ...). Mount your own YAML and
# override CONFIG_FILE to bypass it.
ENV OUTPUT_DIR=/data/output \
    CONFIG_FILE=/app/config.docker.yaml \
    LOG_LEVEL=INFO \
    MERGE_AFTER_RUN=true \
    PYTHONUNBUFFERED=1

# NOTE: we intentionally do NOT `USER scraper` here. A bind-mounted volume is
# owned by the host (often root), which masks the chown above, so the container
# starts as root, fixes ownership of the mounted output dir in the entrypoint,
# then drops to `scraper` via gosu. See docker-entrypoint.sh.

# tini as PID 1 so Ctrl-C / `docker stop` reaches the app and it checkpoints.
ENTRYPOINT ["tini", "--", "/app/docker-entrypoint.sh"]
# No CMD on purpose: with no arguments the entrypoint picks `manager` when the
# scraper manager passed `scraping_args`, else `run`. Override at
# `docker run ... <cmd>` (e.g. `filter`, `run --dry-run`).

# Merging a scraped subreddit into one file

`merge_output.py` (project root) turns a subreddit's archive — which the
scraper stores as two id-keyed maps, split by record kind — into a single flat,
chronological JSON array.

```
data/romania/posts.json      {"1rhu4yd": {...}, "1rhv2k1": {...}, ...}
data/romania/comments.json   {"p261erx": {...}, "p26wz35": {...}, ...}
        ↓  python3 merge_output.py
data/romania/romania_merged.json   [ {...}, {...}, ... ]
```

The merged file is written **next to** the `posts.json` / `comments.json` it was
built from, and is named after the folder: `<subreddit>_merged.json`.

## It already runs itself

Under `docker compose`, a finished `run` chains this script over the mounted
output directory, so `<subreddit>_merged.json` appears on the host without you
doing anything:

```
run finished; merging /data/output
romania: 20678 post(s) + 13557 comment(s) -> /data/output/romania/romania_merged.json
```

`MERGE_AFTER_RUN: "false"` in `docker-compose.yml` turns that off. A failed
archive never merges. To merge an archive already on disk — after turning the
step off, or after running phase 2 so the flags below are carried through:

```bash
docker compose run --rm scraper-a merge     # the whole mounted output dir
```

The rest of this page is the script itself, for running it on the host or
against a folder the container never saw.

## Usage

```bash
python3 merge_output.py                 # every subreddit under ./data
python3 merge_output.py /srv/reddit     # a different output root (see DATA_DIR)
python3 merge_output.py data/romania    # just one subreddit folder
python3 merge_output.py --indent 0      # one compact line instead of indented
```

| Argument | Default | Meaning |
|----------|---------|---------|
| `path` | `data` | Output root to scan, **or** a single subreddit folder. |
| `--indent` | `2` | JSON indentation. `0` writes the whole array on one line (smaller file, harder to read). |

The script uses only the Python standard library, so it runs on the host with a
plain `python3` — no virtualenv, no installed package, nothing to build. It
never contacts the network and never modifies `posts.json` / `comments.json`.

Running it on a subreddit that is still being scraped is safe but gives you a
snapshot of that moment; re-run it when the scrape is done (`docker compose ps`
shows the container as `Exited (0)`).

## What comes out

One array, sorted by `created_epoch` ascending — so posts and their comments are
interleaved in the order Reddit produced them. Records that share a timestamp
are ordered by `id`, so two runs over the same archive produce byte-identical
files.

Every record carries a `type` of `"post"` or `"comment"`, then that kind's
fields in a fixed order:

**`"type": "comment"`**

| Field | Notes |
|-------|-------|
| `id` | Reddit's base-36 comment id, no `t1_` prefix. |
| `post_id` | Id of the post it belongs to, no `t3_` prefix. |
| `parent_id` | Still prefixed: `t3_<post>` for a top-level comment, `t1_<comment>` for a reply. |
| `subreddit`, `author`, `body`, `score` | `author` is `null` when the account is gone. |
| `depth` | 0 for top-level, +1 per reply hop. Materialized after the comment walk finishes. |
| `created_utc` / `created_epoch` | ISO-8601 UTC and unix seconds for the same instant. |
| `is_deleted` | True when Reddit returned `[deleted]` / `[removed]`. |

**`"type": "post"`**

| Field | Notes |
|-------|-------|
| `id`, `subreddit`, `author`, `title`, `selftext` | `selftext` is `""` for link posts. |
| `url` | What the post points at — the link target, or the post itself for a text post. |
| `permalink` | Reddit path, e.g. `/r/CasualRO/comments/1vhos36/...`. |
| `score`, `num_comments` | As of the moment the record was last fetched. |
| `created_utc` / `created_epoch`, `is_deleted`, `over_18` | |

A field the archive doesn't have is written as `null` rather than omitted, so
every record of a kind has the same shape.

Example:

```json
[
  {
    "type": "comment",
    "id": "p261erx",
    "post_id": "1vhd4s5",
    "parent_id": "t1_p24980u",
    "subreddit": "casualro",
    "author": "BrosCoiulFermecat",
    "body": "Mucegaiul creaza o mica doza de penicilina...",
    "score": -12,
    "depth": 0,
    "created_utc": "2026-08-07T00:04:38+00:00",
    "created_epoch": 1786061078,
    "is_deleted": false
  },
  {
    "type": "post",
    "id": "1vhos36",
    "subreddit": "casualro",
    "author": "Think-Zucchini9399",
    "title": "Imi pun ca si fundal la alarma GIF-ul cu cele mai multe likeuri",
    "selftext": "Am descoperit ca poti pune GIF-uri ca si fundal de alarma...",
    "url": "https://www.reddit.com/r/CasualRO/comments/1vhos36/...",
    "permalink": "/r/CasualRO/comments/1vhos36/...",
    "score": 0,
    "num_comments": 17,
    "created_utc": "2026-08-07T02:56:58+00:00",
    "created_epoch": 1786071418,
    "is_deleted": false,
    "over_18": false
  }
]
```

### What is left out

- **`raw`** — the provider's untouched record, which is most of the archive's
  size. It stays in `posts.json` / `comments.json`; the merged file is the
  normalized view.
- **`fetched_at` / `updated_at`** — bookkeeping for the idempotent upsert, not
  content.
- **`flagged` / `flag_reason` / `matched_terms`** are the exception: these are
  carried through when [phase 2](../DOCKER.md#phase-2-keyword-export-optional-after-collecting)
  has already annotated the archive. Merge *after* running the filter if you
  want them.

## Which folders it picks up

Any directory containing a `posts.json` or a `comments.json`, searched
recursively from `path`. That covers both layouts the scraper writes:

```
data/<subreddit>/                              ← normal runs
data/reddit_<arg-id>_<label>_.../<subreddit>/  ← scraper-manager jobs
```

A folder with neither file — a subreddit that returned nothing, so only
`subreddit.json` and `progress.json` exist — is skipped silently.

## Output and exit codes

```
$ python3 merge_output.py
romania: 20678 post(s) + 13557 comment(s) -> data/romania/romania_merged.json
merged 34235 record(s) from 1 subreddit folder(s)
```

| Exit code | Meaning |
|-----------|---------|
| `0` | Every folder merged. |
| `1` | At least one folder failed (e.g. a corrupt `posts.json`). The error names the folder and the reason on stderr; the other folders are still merged. |
| `2` | `path` is not a directory, or nothing under it holds a `posts.json` / `comments.json`. Nothing was written. |

Writes are atomic — the file goes to a temp file in the same directory and is
then moved into place — so an interrupted run can never leave a half-written
`<subreddit>_merged.json` behind.

## Notes

- **Memory.** The whole archive is loaded to sort it, so a large subreddit needs
  roughly a few times the size of `posts.json` in RAM. A year of `r/romania`
  (~190 MB of JSON on disk) merges comfortably on a normal laptop; if you ever
  hit a limit, merge one folder at a time by passing its path.
- **Re-running is safe.** The merged file is rebuilt from scratch each time and
  is never read back as input.
- **Same shape as manager mode.** The record layout matches the `results.json`
  that scraper-manager jobs already produce
  (`src/reddit_archiver/manager/results.py`), so anything that consumes one
  consumes the other. The difference is scope: `results.json` covers a whole
  job folder, `<subreddit>_merged.json` covers one subreddit.
- Where these records come from is drawn in
  [record-pipeline.svg](record-pipeline.svg).

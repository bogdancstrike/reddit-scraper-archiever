import json

from reddit_archiver.models import normalize_comment, normalize_post
from reddit_archiver.store import JsonProgressStore, JsonStorage


def _read(path):
    return json.loads(path.read_text())


async def test_upsert_posts_and_comments_are_idempotent(tmp_path):
    store = JsonStorage(tmp_path)
    await store.upsert_subreddit("python")

    post = normalize_post(
        {"id": "t3_p1", "author": "a", "title": "T", "created_utc": 1_600_000_000,
         "score": 5, "subreddit": "python"},
        "python",
    )
    assert await store.upsert_posts([post]) == 1

    # Re-upsert with a refreshed score; must not duplicate, must refresh.
    post2 = normalize_post(
        {"id": "t3_p1", "author": "a", "title": "T", "created_utc": 1_600_000_000,
         "score": 42, "subreddit": "python"},
        "python",
    )
    await store.upsert_posts([post2])

    posts = _read(tmp_path / "python" / "posts.json")
    assert list(posts) == ["p1"]
    assert posts["p1"]["score"] == 42
    # fetched_at preserved from first insert; updated_at advanced.
    assert posts["p1"]["fetched_at"] <= posts["p1"]["updated_at"]


async def test_recompute_comment_depths(tmp_path):
    store = JsonStorage(tmp_path)
    raws = [
        {"id": "c0", "link_id": "t3_post", "parent_id": "t3_post", "body": "root",
         "author": "a", "created_utc": 1},
        {"id": "c1", "link_id": "t3_post", "parent_id": "t1_c0", "body": "child",
         "author": "b", "created_utc": 2},
        {"id": "c2", "link_id": "t3_post", "parent_id": "t1_c1", "body": "grandchild",
         "author": "c", "created_utc": 3},
    ]
    comments = [normalize_comment(r, "python") for r in raws]
    await store.upsert_comments(comments)
    await store.recompute_comment_depths("python")

    stored = _read(tmp_path / "python" / "comments.json")
    assert stored["c0"]["depth"] == 0
    assert stored["c1"]["depth"] == 1
    assert stored["c2"]["depth"] == 2


async def test_progress_roundtrip_and_resume(tmp_path):
    progress = JsonProgressStore(tmp_path)
    assert await progress.get("python", "arctic_shift", "posts") is None

    await progress.upsert(
        subreddit="python", backend="arctic_shift", kind="posts",
        window_start_epoch=1, window_end_epoch=100, cursor_epoch=50,
        status="in_progress", items_fetched=7,
    )
    got = await progress.get("python", "arctic_shift", "posts")
    assert got is not None
    assert got.cursor_epoch == 50
    assert got.items_fetched == 7
    assert got.status == "in_progress"

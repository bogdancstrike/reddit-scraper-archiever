from datetime import timezone

from reddit_archiver.models import normalize_comment, normalize_post


def test_normalize_post_strips_prefix_and_marks_deleted():
    p = normalize_post(
        {"name": "t3_abc123", "author": "[deleted]", "title": "T",
         "selftext": "[removed]", "created_utc": 1_600_000_000, "score": 5,
         "subreddit": "python"},
        "python",
    )
    assert p.id == "abc123"
    assert p.author is None
    assert p.is_deleted is True
    assert p.created_utc.tzinfo == timezone.utc
    assert p.score == 5


def test_normalize_comment_keeps_prefixed_parent_and_post_id():
    c = normalize_comment(
        {"id": "cmt1", "link_id": "t3_post9", "parent_id": "t1_cmt0",
         "author": "bob", "body": "hi", "created_utc": "1600000000"},
        "python",
    )
    assert c.id == "cmt1"
    assert c.post_id == "post9"            # stripped
    assert c.parent_id == "t1_cmt0"        # kept prefixed for tree rebuild
    assert c.is_deleted is False


def test_normalize_comment_without_link_id_is_dropped():
    assert normalize_comment({"id": "x", "body": "y"}, "s") is None

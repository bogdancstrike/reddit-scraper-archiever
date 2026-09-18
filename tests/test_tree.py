from reddit_archiver.models import normalize_comment
from reddit_archiver.tree import build_forest


def _c(cid, parent, link="t3_post1", body="x"):
    return normalize_comment(
        {"id": cid, "parent_id": parent, "link_id": link, "author": "u",
         "body": body, "created_utc": 1000, "subreddit": "s"},
        "s",
    )


def test_build_forest_depths():
    # post1: a (top) -> b -> c ; d (top)
    comments = [
        _c("a", "t3_post1"),
        _c("b", "t1_a"),
        _c("c", "t1_b"),
        _c("d", "t3_post1"),
    ]
    roots = build_forest(comments)
    assert {r.comment.id for r in roots} == {"a", "d"}
    a = next(r for r in roots if r.comment.id == "a")
    assert a.depth == 0
    assert a.children[0].comment.id == "b"
    assert a.children[0].depth == 1
    assert a.children[0].children[0].comment.id == "c"
    assert a.children[0].children[0].depth == 2


def test_orphan_treated_as_root():
    # parent 'zzz' not present -> child becomes a root, nothing dropped.
    comments = [_c("child", "t1_zzz")]
    roots = build_forest(comments)
    assert len(roots) == 1
    assert roots[0].comment.id == "child"
    assert roots[0].depth == 0

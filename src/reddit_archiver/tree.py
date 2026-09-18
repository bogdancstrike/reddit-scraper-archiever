"""Local comment-tree reconstruction.

The archive is fetched as a FLAT stream of comments. Each comment carries
``link_id`` (its post) and ``parent_id`` (t3_<post> for a top-level comment,
t1_<comment> otherwise). We never fetch trees post-by-post; instead we rebuild
the hierarchy from these fields. In the database this is materialized as a
``depth`` column (see storage.recompute_comment_depths); this module provides
the equivalent in-memory reconstruction used by --dry-run and as a utility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .models import NormalizedComment


@dataclass
class TreeNode:
    comment: NormalizedComment
    depth: int
    children: list["TreeNode"] = field(default_factory=list)


def build_forest(comments: Iterable[NormalizedComment]) -> list[TreeNode]:
    """Reconstruct comment trees for one or more posts from a flat list.

    Returns the top-level nodes (roots), each with nested children and a
    computed depth. Orphans (parent not present in the batch) are treated as
    roots so nothing is dropped.
    """
    by_id: dict[str, NormalizedComment] = {c.id: c for c in comments}
    nodes: dict[str, TreeNode] = {}

    def parent_comment_id(c: NormalizedComment) -> str | None:
        pid = c.parent_id
        if pid and pid.startswith("t1_"):
            return pid[3:]
        return None  # parent is the post (t3_) -> top-level

    def make_node(cid: str) -> TreeNode:
        if cid in nodes:
            return nodes[cid]
        node = TreeNode(comment=by_id[cid], depth=0)
        nodes[cid] = node
        return node

    roots: list[TreeNode] = []
    for cid, comment in by_id.items():
        node = make_node(cid)
        pcid = parent_comment_id(comment)
        if pcid and pcid in by_id:
            parent = make_node(pcid)
            parent.children.append(node)
        else:
            roots.append(node)

    # Assign depths via BFS from roots.
    for root in roots:
        _assign_depth(root, 0)
    return roots


def _assign_depth(node: TreeNode, depth: int) -> None:
    node.depth = depth
    for child in node.children:
        _assign_depth(child, depth + 1)


def format_forest(roots: list[TreeNode], max_lines: int = 40) -> str:
    """Render a compact indented preview of the reconstructed trees."""
    lines: list[str] = []

    def walk(node: TreeNode) -> None:
        if len(lines) >= max_lines:
            return
        author = node.comment.author or "[deleted]"
        body = (node.comment.body or "").replace("\n", " ")
        if len(body) > 70:
            body = body[:67] + "..."
        lines.append(f"{'  ' * node.depth}- ({author}) {body}")
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)
    if len(lines) >= max_lines:
        lines.append("  ...")
    return "\n".join(lines)

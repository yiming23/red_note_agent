"""Manually log post-publish metrics back to the posts table.

Usage examples:
    # Mark as published (locks in the published_at timestamp)
    python scripts/log_post_result.py --id 42 --decision published

    # Update 24h metrics
    python scripts/log_post_result.py --id 42 --period 24h \\
        --views 1200 --likes 89 --saves 45 --comments 12

    # Update 7d metrics
    python scripts/log_post_result.py --id 42 --period 7d \\
        --views 3400 --likes 210 --saves 130 --comments 38

    # Add a note
    python scripts/log_post_result.py --id 42 --note "评论引发争议"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import PostState
from xhs_agent.storage.repositories import PostRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Update a post's published-state metrics.")
    parser.add_argument("--id", type=int, required=True, help="post id")
    parser.add_argument("--decision", choices=["published", "rejected"], default=None)
    parser.add_argument("--period", choices=["24h", "7d"], default=None)
    parser.add_argument("--views", type=int, default=None)
    parser.add_argument("--likes", type=int, default=None)
    parser.add_argument("--saves", type=int, default=None)
    parser.add_argument("--comments", type=int, default=None)
    parser.add_argument("--note", type=str, default=None)
    parser.add_argument("--edit-notes", type=str, default=None,
                        help="record what you edited before publishing")
    args = parser.parse_args()

    with session_scope() as s:
        post = PostRepository(s).get(args.id)
        if post is None:
            print(f"❌ post #{args.id} not found")
            sys.exit(1)

        if args.decision == "published":
            post.state = PostState.PUBLISHED
            post.published_at = datetime.utcnow()
        elif args.decision == "rejected":
            post.state = PostState.REJECTED

        if args.edit_notes is not None:
            post.edit_notes = args.edit_notes

        if args.period == "24h":
            if args.views is not None: post.views_24h = args.views
            if args.likes is not None: post.likes_24h = args.likes
            if args.saves is not None: post.saves_24h = args.saves
            if args.comments is not None: post.comments_24h = args.comments
        elif args.period == "7d":
            if args.views is not None: post.views_7d = args.views
            if args.likes is not None: post.likes_7d = args.likes
            if args.saves is not None: post.saves_7d = args.saves
            if args.comments is not None: post.comments_7d = args.comments

        # Recompute engagement_rate using best available window (prefer 7d)
        v = post.views_7d or post.views_24h
        if v and v > 0:
            l = post.likes_7d or post.likes_24h or 0
            sv = post.saves_7d or post.saves_24h or 0
            c = post.comments_7d or post.comments_24h or 0
            post.engagement_rate = (l + sv * 2 + c * 4) / v

        if args.note:
            existing = post.notes or ""
            sep = "\n" if existing else ""
            post.notes = existing + sep + args.note

        print(f"✓ post #{post.id} updated. state={post.state.value}, engagement_rate={post.engagement_rate}")


if __name__ == "__main__":
    main()

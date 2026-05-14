"""Drop all rows in posts table (schema preserved).

Use when changing persona/templates and you don't want v4-era candidates polluting V5 review.

Usage:
    uv run python scripts/reset_posts.py            # interactive confirm
    uv run python scripts/reset_posts.py --yes      # skip confirm
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import delete, func, select

from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import Post


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="skip confirmation")
    args = parser.parse_args()

    with session_scope() as s:
        count = s.scalar(select(func.count()).select_from(Post))
        if count == 0:
            print("posts table is already empty.")
            return
        if not args.yes:
            print(f"This will DELETE {count} row(s) from posts table.")
            resp = input("Type 'yes' to confirm: ").strip().lower()
            if resp != "yes":
                print("Aborted.")
                sys.exit(1)
        s.execute(delete(Post))

    print(f"✓ deleted {count} row(s) from posts.")


if __name__ == "__main__":
    main()

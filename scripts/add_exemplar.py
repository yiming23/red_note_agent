"""Manually add a 小红书 exemplar post (爆款样本) to the exemplar_posts table.

Used by Yiming when he sees a same-domain post he wants to use as style reference.

Usage:
    # paste content directly
    python scripts/add_exemplar.py --template hidden_gem --content "..."

    # with a screenshot
    python scripts/add_exemplar.py --template negative_review_burst \\
        --content "..." --screenshot ~/Pictures/xhs_screenshot.png

    # with style tags
    python scripts/add_exemplar.py --template hidden_gem \\
        --content "..." --style-tags "吐槽,高密度梗,反预期开头"

DESIGN.md § 7.4.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from xhs_agent.config import settings
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import ExemplarSource
from xhs_agent.storage.repositories import ExemplarRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True,
                        help="negative_review_burst / hidden_gem / comeback_game / "
                             "new_release_heat / player_spike_event")
    parser.add_argument("--content", required=True, help="full post text")
    parser.add_argument("--screenshot", type=Path, default=None,
                        help="path to a screenshot of the original post")
    parser.add_argument("--source", choices=[s.value for s in ExemplarSource],
                        default="manual")
    parser.add_argument("--style-tags", default="",
                        help="comma-separated style descriptors, e.g. '吐槽,反预期开头'")
    parser.add_argument("--engagement", default=None,
                        help='JSON metrics, e.g. \'{"likes": 1200, "saves": 800}\'')
    args = parser.parse_args()

    screenshot_path: str | None = None
    if args.screenshot:
        if not args.screenshot.exists():
            print(f"❌ screenshot not found: {args.screenshot}")
            sys.exit(1)
        # Copy into project's exemplar_screenshots/ to keep it stable
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dest_name = f"{ts}_{args.screenshot.name}"
        dest = settings.exemplar_screenshots_dir / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(args.screenshot, dest)
        screenshot_path = str(dest.relative_to(settings.exemplar_screenshots_dir.parent))

    style_tags = [t.strip() for t in args.style_tags.split(",") if t.strip()]

    engagement_metrics = None
    if args.engagement:
        import json as json_module
        try:
            engagement_metrics = json_module.loads(args.engagement)
        except json_module.JSONDecodeError as e:
            print(f"❌ --engagement must be valid JSON: {e}")
            sys.exit(1)

    with session_scope() as s:
        ex = ExemplarRepository(s).add(
            source_platform=ExemplarSource(args.source),
            raw_content=args.content,
            screenshot_path=screenshot_path,
            domain="games",
            template_match=args.template,
            style_tags=style_tags or None,
            engagement_metrics=engagement_metrics,
        )
        ex_id = ex.id

    print(f"✓ exemplar #{ex_id} added (template={args.template})")
    if screenshot_path:
        print(f"  screenshot saved at {screenshot_path}")


if __name__ == "__main__":
    main()

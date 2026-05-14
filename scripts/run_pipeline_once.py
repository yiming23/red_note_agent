"""Manually trigger one content pipeline run.

Usage:
    uv run python scripts/run_pipeline_once.py
    uv run python scripts/run_pipeline_once.py --no-push   # skip Telegram
    uv run python scripts/run_pipeline_once.py --limit 5
"""

from __future__ import annotations

import argparse
import sys

# Ensure src/ is importable without installing the package
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from xhs_agent.observability.logger import get_logger
from xhs_agent.orchestration.pipeline import run_content_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run content pipeline once.")
    parser.add_argument("--limit", type=int, default=20, help="Per-collector entity limit")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--no-push", action="store_true", help="Skip Telegram push")
    args = parser.parse_args()

    log = get_logger("run_pipeline_once")
    log.info("manual_trigger", **vars(args))

    result = run_content_pipeline(
        collect_limit=args.limit,
        max_candidates_per_run=args.max_candidates,
        push_to_telegram=not args.no_push,
    )

    print()
    print(f"Pipeline run #{result.run_id} done.")
    print(f"  signals detected:  {result.signals_detected}")
    print(f"  signals approved:  {result.signals_approved}")
    print(f"  posts generated:   {result.posts_generated}")
    print(f"  posts pushed:      {result.posts_pushed}")
    if result.errors:
        print("  errors:")
        for e in result.errors:
            print(f"    - {e}")


if __name__ == "__main__":
    main()

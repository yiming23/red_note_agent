"""Quick DB inspection — useful when debugging.

Usage:
    python scripts/inspect_db.py                # summary counts
    python scripts/inspect_db.py --posts        # list recent posts
    python scripts/inspect_db.py --signals      # list recent signals
    python scripts/inspect_db.py --budget       # today's LLM spend
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select, func

from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import (
    GameSignal,
    LlmCall,
    MemePhrase,
    PipelineRun,
    Post,
    Watchlist,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--posts", action="store_true")
    parser.add_argument("--signals", action="store_true")
    parser.add_argument("--budget", action="store_true")
    parser.add_argument("--watchlist", action="store_true")
    parser.add_argument("--memes", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    with session_scope() as s:
        if not any([args.posts, args.signals, args.budget, args.watchlist, args.memes]):
            print("=== Counts ===")
            for model in (Post, GameSignal, Watchlist, MemePhrase, PipelineRun, LlmCall):
                count = s.scalar(select(func.count()).select_from(model))
                print(f"  {model.__tablename__:<20} {count}")

        if args.posts:
            print(f"\n=== Recent posts (top {args.limit}) ===")
            for p in s.scalars(
                select(Post).order_by(Post.generated_at.desc()).limit(args.limit)
            ):
                print(
                    f"  #{p.id} [{p.state.value}] {p.title[:30]!r} "
                    f"({p.template_used}) - {p.trigger_entity_name} - "
                    f"${p.llm_cost_usd or 0:.4f}"
                )

        if args.signals:
            print(f"\n=== Recent signals (top {args.limit}) ===")
            for sig in s.scalars(
                select(GameSignal).order_by(GameSignal.detected_at.desc()).limit(args.limit)
            ):
                print(
                    f"  [{sig.severity.value}] {sig.game_name} - {sig.signal_type.value} "
                    f"(score={sig.score})"
                )

        if args.watchlist:
            print(f"\n=== Watchlist (top {args.limit}) ===")
            for w in s.scalars(select(Watchlist).limit(args.limit)):
                print(f"  [{w.status.value}] appid={w.appid} {w.game_name}")

        if args.memes:
            print(f"\n=== Memes (top {args.limit}) ===")
            for m in s.scalars(
                select(MemePhrase).order_by(MemePhrase.frequency_score.desc()).limit(args.limit)
            ):
                print(
                    f"  [{m.status.value}] '{m.phrase}' "
                    f"freq={m.frequency_score:.2f} count={m.occurrence_count} ({m.source_platform})"
                )

        if args.budget:
            from xhs_agent.budget.guard import get_status
            st = get_status()
            print("\n=== Today's LLM spend ===")
            print(f"  spent: ${st['spent_today_usd']}")
            print(f"  budget: ${st['budget_usd']}")
            print(f"  remaining: ${st['remaining_usd']}")
            print(f"  pct: {st['pct_used']}%")


if __name__ == "__main__":
    main()

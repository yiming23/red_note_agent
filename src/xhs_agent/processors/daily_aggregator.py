"""Daily review stats aggregator — builds DailyReviewStats rows from entity pools.

Called once per day by the scheduler. For each entity that was collected today,
aggregates the review pool into a DailyReviewStats row and computes a 7-day
rolling positive rate by reading the last 7 rows.

This feeds the S7 positive-rate timeline chart.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.repositories import DailyReviewStatsRepository

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity

log = get_logger(__name__)


def aggregate_daily(entities: list["GameEntity"]) -> int:
    """Write today's DailyReviewStats for all entities. Returns count of rows written."""
    today = date.today().isoformat()
    written = 0

    for entity in entities:
        pool = getattr(entity, "recent_review_pool", None) or []
        if not pool:
            continue

        pos_count = sum(1 for r in pool if r.get("voted_up"))
        neg_count = sum(1 for r in pool if not r.get("voted_up"))
        total = len(pool)
        daily_pos_rate = pos_count / total if total else None

        try:
            with session_scope() as session:
                repo = DailyReviewStatsRepository(session)
                repo.upsert(
                    appid=entity.appid,
                    date=today,
                    pos_count=pos_count,
                    neg_count=neg_count,
                    total_count=total,
                    daily_pos_rate=daily_pos_rate,
                    rolling_7d_pos_rate=_compute_rolling_7d(
                        repo, entity.appid, today, daily_pos_rate
                    ),
                )
                written += 1
        except Exception as exc:
            log.warning("daily_aggregator_failed", appid=entity.appid, error=str(exc))

    log.info("daily_aggregator_done", written=written, total_entities=len(entities))
    return written


def _compute_rolling_7d(
    repo: DailyReviewStatsRepository,
    appid: str,
    today: str,
    today_rate: float | None,
) -> float | None:
    """Compute 7-day rolling positive rate from DB rows."""
    rows = repo.get_for_appid(appid, days=7)
    rates = [r.daily_pos_rate for r in rows if r.daily_pos_rate is not None and r.date != today]
    if today_rate is not None:
        rates.append(today_rate)
    if not rates:
        return None
    return round(sum(rates) / len(rates), 4)

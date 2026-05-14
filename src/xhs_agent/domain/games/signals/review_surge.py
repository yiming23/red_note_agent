"""Signal: 评论数激增 — review-writing rate is unusually high (heat proxy).

DESIGN.md § 6 #5.

Trigger:    24h_review_count / 30d_avg_daily_reviews > 3.0

Like player_spike, this needs a baseline. V0 fallback: compare 24h count
against (total_reviews / days_active). If 24h count is >5x that lifetime
daily average, flag as surge.
"""

from __future__ import annotations

from typing import Callable, Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

BaselineProvider = Callable[[str], Optional[float]]
"""Function (appid) -> 30d avg daily reviews, or None."""


class ReviewSurgeDetector(SignalDetector[GameEntity]):
    signal_type = "review_surge"

    def __init__(self, baseline_provider: Optional[BaselineProvider] = None) -> None:
        self.baseline_provider = baseline_provider

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        params = tuning.games.signals.review_surge

        recent_24h = entity.recent_24h_review_count
        if not recent_24h or recent_24h < 5:
            # Too few reviews to even compute a meaningful velocity
            return None

        # Preferred: 30d daily average baseline
        baseline: Optional[float] = None
        if self.baseline_provider is not None:
            baseline = self.baseline_provider(entity.appid)

        if baseline and baseline > 0:
            velocity = recent_24h / baseline
            if velocity < params.velocity_threshold:
                return None
            return SignalResult(
                entity_id=entity.appid,
                entity_name=entity.name,
                signal_type=self.signal_type,
                score=round(min(velocity / 10.0, 1.5), 3),
                severity="normal",
                raw_data={
                    "recent_24h_review_count": recent_24h,
                    "baseline_30d_daily_avg": round(baseline, 2),
                    "velocity": round(velocity, 2),
                    "method": "baseline",
                },
            )

        # Fallback: lifetime daily average from total_reviews / age
        if (
            entity.total_reviews
            and entity.game_age_days
            and entity.game_age_days > 30
        ):
            lifetime_daily = entity.total_reviews / entity.game_age_days
            if lifetime_daily <= 0:
                return None
            velocity = recent_24h / lifetime_daily
            if velocity < params.fallback_velocity_threshold:
                return None
            return SignalResult(
                entity_id=entity.appid,
                entity_name=entity.name,
                signal_type=self.signal_type,
                score=round(min(velocity / 20.0, 1.5), 3),
                severity="normal",
                raw_data={
                    "recent_24h_review_count": recent_24h,
                    "lifetime_daily_avg": round(lifetime_daily, 2),
                    "velocity": round(velocity, 2),
                    "method": "fallback_lifetime_avg",
                },
            )

        log.debug("review_surge_no_baseline", appid=entity.appid)
        return None

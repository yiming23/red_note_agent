"""Signal: 好评爆发 / 老游戏复活 — recent positive rate spiked AND old game.

DESIGN.md § 6 #2.

Trigger:    7d positive_rate up > 10% AND 7d review count > 200 AND age > 180d
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)


class PositiveBurstDetector(SignalDetector[GameEntity]):
    signal_type = "positive_burst"

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        params = tuning.games.signals.positive_burst

        recent = entity.recent_7d_positive_rate
        historical = entity.historical_positive_rate
        volume = entity.recent_7d_review_count or 0
        age = entity.game_age_days

        if recent is None or historical is None:
            log.debug("positive_burst_missing_data", appid=entity.appid)
            return None
        if age is None or age < params.min_age_days:
            return None

        rise = recent - historical
        if rise <= params.rise_threshold or volume < params.volume_threshold:
            return None

        score = min(rise / 0.30, 1.5) * (1.0 + (volume / 1000) * 0.5)

        return SignalResult(
            entity_id=entity.appid,
            entity_name=entity.name,
            signal_type=self.signal_type,
            score=round(score, 3),
            severity="normal",  # comeback is rarely "urgent" — slow burn story
            raw_data={
                "historical_positive_rate": round(historical, 3),
                "recent_7d_positive_rate": round(recent, 3),
                "rise": round(rise, 3),
                "recent_7d_review_count": volume,
                "game_age_days": age,
            },
        )

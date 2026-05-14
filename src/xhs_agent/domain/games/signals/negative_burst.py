"""Signal: 差评爆发 — recent positive rate dropped sharply vs historical baseline.

DESIGN.md § 6 #1.

Trigger:    7d positive_rate drop > 15% AND 7d review count > 100
Strong:     drop > 30% OR 7d review count > 500
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)


class NegativeBurstDetector(SignalDetector[GameEntity]):
    signal_type = "negative_burst"

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        params = tuning.games.signals.negative_burst

        recent = entity.recent_7d_positive_rate
        historical = entity.historical_positive_rate
        volume = entity.recent_7d_review_count or 0

        if recent is None or historical is None:
            log.debug("negative_burst_missing_data", appid=entity.appid)
            return None

        drop = historical - recent  # positive number = positive rate fell
        if drop <= params.drop_threshold or volume < params.volume_threshold:
            return None

        is_strong = drop > params.drop_strong_threshold or volume > params.volume_strong_threshold
        score = min(drop / params.drop_strong_threshold, 1.5) * (
            1.0 + (volume / params.volume_strong_threshold) * 0.5
        )
        severity = "urgent" if is_strong else "normal"

        return SignalResult(
            entity_id=entity.appid,
            entity_name=entity.name,
            signal_type=self.signal_type,
            score=round(score, 3),
            severity=severity,
            raw_data={
                "historical_positive_rate": round(historical, 3),
                "recent_7d_positive_rate": round(recent, 3),
                "drop": round(drop, 3),
                "recent_7d_review_count": volume,
                "is_strong": is_strong,
            },
        )

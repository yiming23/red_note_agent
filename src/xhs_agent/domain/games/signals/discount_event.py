"""Signal: 折扣值不值 — game is on sale with sufficient review volume.

Triggers when a game is currently discounted (≥20%) AND has enough recent
reviews to form an opinion.  The content_type maps to "折扣值不值".
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)


class DiscountEventDetector(SignalDetector[GameEntity]):
    signal_type = "discount_event"

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        if not entity.is_on_special:
            return None

        discount = entity.discount_pct or 0
        params = tuning.games.signals.discount_event

        if discount < params.min_discount_pct:
            return None

        reviews = entity.recent_reviews_count or 0
        if reviews < params.min_reviews:
            return None

        severity = "urgent" if discount >= params.urgent_discount_pct else "normal"

        return SignalResult(
            entity_id=entity.appid,
            entity_name=entity.name,
            signal_type=self.signal_type,
            score=round(min(discount / 100, 1.0), 3),
            severity=severity,
            raw_data={
                "discount_pct": discount,
                "original_price": entity.original_price,
                "final_price": entity.final_price,
                "recent_reviews_count": reviews,
                "historical_positive_rate": entity.historical_positive_rate,
            },
        )

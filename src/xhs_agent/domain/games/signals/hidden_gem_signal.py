"""Signal: 小众神作 — low review volume but very high historical positive rate.

Detects niche games that are well-loved but fly under the radar.
Content type maps to "小众神作".
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)


class HiddenGemDetector(SignalDetector[GameEntity]):
    signal_type = "hidden_gem"

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        params = tuning.games.signals.hidden_gem

        total = entity.total_reviews or 0
        if not (params.min_reviews <= total <= params.max_reviews):
            return None

        pos_rate = entity.historical_positive_rate
        if pos_rate is None or pos_rate < params.min_positive_rate:
            return None

        age = entity.game_age_days
        if age is None or age < params.min_age_days:
            return None

        return SignalResult(
            entity_id=entity.appid,
            entity_name=entity.name,
            signal_type=self.signal_type,
            score=round(pos_rate, 3),
            severity="normal",
            raw_data={
                "total_reviews": total,
                "historical_positive_rate": pos_rate,
                "game_age_days": age,
            },
        )

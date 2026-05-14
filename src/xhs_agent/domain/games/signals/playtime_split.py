"""Signal: 评论区反差 — short-term negative reviews coexist with long-term positive ones.

Detects the "玩了 80 小时还给差评" pattern (or its inverse: newcomers rage-quit but
veterans love it). Requires playtime_buckets to have been computed from the review pool.
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger
from xhs_agent.processors.playtime_buckets import compute_buckets

log = get_logger(__name__)


class PlaytimeSplitDetector(SignalDetector[GameEntity]):
    signal_type = "playtime_split"

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        pool = getattr(entity, "recent_review_pool", None)
        if not pool:
            return None

        params = tuning.games.signals.playtime_split
        buckets = compute_buckets(entity)

        if buckets.total < params.min_reviews_in_pool:
            return None

        short_neg_share = buckets.short_neg_share()
        long_pos_share = buckets.long_pos_share()

        if short_neg_share < params.short_neg_share_threshold:
            return None
        if long_pos_share < params.long_pos_share_threshold:
            return None

        return SignalResult(
            entity_id=entity.appid,
            entity_name=entity.name,
            signal_type=self.signal_type,
            score=round((short_neg_share + long_pos_share) / 2, 3),
            severity="normal",
            raw_data={
                "short_neg": buckets.short_neg,
                "short_neg_share": round(short_neg_share, 3),
                "long_neg": buckets.long_neg,
                "long_pos": buckets.long_pos,
                "long_pos_share": round(long_pos_share, 3),
                "total_pool": buckets.total,
            },
        )

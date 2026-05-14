"""Signal: 新品爆款 — recently released AND charted high.

DESIGN.md § 6 #4.

Trigger:    age < 7 days AND (top_seller_rank < 20 OR current_players > 5000)
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity


class NewReleaseSpikeDetector(SignalDetector[GameEntity]):
    signal_type = "new_release_spike"

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        params = tuning.games.signals.new_release_spike

        age = entity.game_age_days
        if age is None or age >= params.max_age_days:
            return None

        rank_ok = (
            entity.top_seller_rank is not None
            and entity.top_seller_rank <= params.top_seller_rank_cutoff
        )
        players_ok = (
            entity.current_player_count is not None
            and entity.current_player_count >= params.player_threshold
        )
        if not (rank_ok or players_ok):
            return None

        # Score: combination of ranking and concurrent players, normalized
        rank_score = 0.0
        if rank_ok and entity.top_seller_rank is not None:
            rank_score = 1.0 - (entity.top_seller_rank / params.top_seller_rank_cutoff)
        player_score = 0.0
        if players_ok and entity.current_player_count is not None:
            player_score = min(entity.current_player_count / 50_000, 1.0)
        score = max(rank_score, player_score)

        # Strong if both conditions OR very high player count
        is_strong = (rank_ok and players_ok) or (
            entity.current_player_count is not None
            and entity.current_player_count >= 30_000
        )
        severity = "urgent" if is_strong else "normal"

        return SignalResult(
            entity_id=entity.appid,
            entity_name=entity.name,
            signal_type=self.signal_type,
            score=round(score, 3),
            severity=severity,
            raw_data={
                "game_age_days": age,
                "top_seller_rank": entity.top_seller_rank,
                "current_player_count": entity.current_player_count,
                "release_date_iso": entity.release_date_iso,
            },
        )

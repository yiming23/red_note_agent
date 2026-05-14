"""Signal: 在线人数异常 — current concurrent players unusually high vs baseline.

DESIGN.md § 6 #3.

Trigger:    current_player_count > 10_000 AND velocity (today / 30d_avg_peak) > 2.0
Strong:     velocity > 5.0

V0 caveat: 30-day baseline tracking isn't built yet (needs daily snapshots).
For now we use a fallback heuristic: compare current_player_count against
all-time peak_in_game. If current is >50% of all-time peak, flag as spike.
This is rougher than the spec but useful until we have baseline data.

When baseline tracking lands (V1), the detector accepts a `baseline_provider`
that returns the rolling 30d peak average for an appid.
"""

from __future__ import annotations

from typing import Callable, Optional

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalDetector, SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

BaselineProvider = Callable[[str], Optional[float]]
"""Function (appid) -> 30d average peak players, or None if unknown."""


class PlayerSpikeDetector(SignalDetector[GameEntity]):
    signal_type = "player_spike"

    def __init__(self, baseline_provider: Optional[BaselineProvider] = None) -> None:
        self.baseline_provider = baseline_provider

    def detect(self, entity: GameEntity) -> Optional[SignalResult]:
        params = tuning.games.signals.player_spike

        current = entity.current_player_count
        if not current or current < params.absolute_threshold:
            return None

        # Preferred path: compare to a real 30d baseline
        baseline: Optional[float] = None
        if self.baseline_provider is not None:
            baseline = self.baseline_provider(entity.appid)

        if baseline and baseline > 0:
            velocity = current / baseline
            if velocity < params.velocity_threshold:
                return None
            severity = "urgent" if velocity >= params.velocity_strong else "normal"
            return SignalResult(
                entity_id=entity.appid,
                entity_name=entity.name,
                signal_type=self.signal_type,
                score=round(min(velocity / params.velocity_strong, 1.5), 3),
                severity=severity,
                raw_data={
                    "current_player_count": current,
                    "baseline_30d_avg": round(baseline, 1),
                    "velocity": round(velocity, 2),
                    "method": "baseline",
                },
            )

        # Fallback: compare to all-time peak — only valid for new/young games.
        # Old evergreen titles (CS2, Dota2) always pass a ratio check; age guard prevents
        # them from appearing as "spikes" every run.
        age = entity.game_age_days
        max_age = params.max_age_for_fallback_days
        if age is not None and age > max_age:
            log.debug(
                "player_spike_fallback_skipped_old_game",
                appid=entity.appid,
                age_days=age,
                max_age=max_age,
            )
            return None

        peak = entity.peak_in_game
        if not peak or peak <= 0:
            log.debug("player_spike_no_baseline_no_peak", appid=entity.appid)
            return None

        ratio = current / peak
        if ratio < params.fallback_ratio_threshold:
            return None

        # Crude severity: if current is >80% of all-time peak, that's "urgent"
        severity = "urgent" if ratio >= 0.8 else "normal"
        return SignalResult(
            entity_id=entity.appid,
            entity_name=entity.name,
            signal_type=self.signal_type,
            score=round(ratio, 3),
            severity=severity,
            raw_data={
                "current_player_count": current,
                "all_time_peak": peak,
                "ratio_to_peak": round(ratio, 3),
                "method": "fallback_peak_ratio",
                "note": "30d baseline not available yet; using all-time peak comparison",
            },
        )

"""Unit tests for the 5 game signal detectors.

Each test crafts a minimal GameEntity that's just barely over (or under) the
threshold and asserts the detector fires (or doesn't) and reports the correct
severity.
"""

from __future__ import annotations

from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.domain.games.signals import (
    NegativeBurstDetector,
    NewReleaseSpikeDetector,
    PlayerSpikeDetector,
    PositiveBurstDetector,
    ReviewSurgeDetector,
)


# ----------------------------------------------------------------
# Negative burst
# ----------------------------------------------------------------


def _entity(**overrides) -> GameEntity:
    e = GameEntity(appid="999", name="TestGame")
    for k, v in overrides.items():
        setattr(e, k, v)
    return e


def test_negative_burst_fires_when_drop_above_threshold():
    e = _entity(
        historical_positive_rate=0.85,
        recent_7d_positive_rate=0.65,  # 20% drop > 15%
        recent_7d_review_count=200,    # > 100
    )
    r = NegativeBurstDetector().detect(e)
    assert r is not None
    assert r.signal_type == "negative_burst"
    assert r.severity == "normal"
    assert r.raw_data["drop"] > 0.15


def test_negative_burst_strong_when_drop_above_30pct():
    e = _entity(
        historical_positive_rate=0.85,
        recent_7d_positive_rate=0.50,  # 35% drop
        recent_7d_review_count=120,
    )
    r = NegativeBurstDetector().detect(e)
    assert r is not None
    assert r.severity == "urgent"


def test_negative_burst_no_fire_when_volume_too_low():
    e = _entity(
        historical_positive_rate=0.85,
        recent_7d_positive_rate=0.50,  # huge drop, but...
        recent_7d_review_count=80,     # below 100 threshold
    )
    assert NegativeBurstDetector().detect(e) is None


def test_negative_burst_no_fire_on_missing_data():
    e = _entity(historical_positive_rate=0.85)  # missing recent rate
    assert NegativeBurstDetector().detect(e) is None


# ----------------------------------------------------------------
# Positive burst (老游戏复活)
# ----------------------------------------------------------------


def test_positive_burst_fires_for_old_game_with_rising_score():
    e = _entity(
        historical_positive_rate=0.70,
        recent_7d_positive_rate=0.85,   # +15% rise
        recent_7d_review_count=300,
        game_age_days=400,              # > 180
    )
    r = PositiveBurstDetector().detect(e)
    assert r is not None
    assert r.signal_type == "positive_burst"


def test_positive_burst_no_fire_on_new_game():
    e = _entity(
        historical_positive_rate=0.70,
        recent_7d_positive_rate=0.85,
        recent_7d_review_count=300,
        game_age_days=30,               # too young — exclude honeymoon
    )
    assert PositiveBurstDetector().detect(e) is None


# ----------------------------------------------------------------
# Player spike
# ----------------------------------------------------------------


def test_player_spike_with_baseline_velocity():
    e = _entity(current_player_count=50_000)
    detector = PlayerSpikeDetector(baseline_provider=lambda _: 10_000.0)
    r = detector.detect(e)
    assert r is not None
    assert r.severity == "urgent"  # velocity 5.0 hits strong threshold
    assert r.raw_data["method"] == "baseline"


def test_player_spike_fallback_uses_all_time_peak():
    e = _entity(current_player_count=60_000, peak_in_game=100_000)
    r = PlayerSpikeDetector().detect(e)
    assert r is not None  # 60% of peak triggers fallback
    assert r.raw_data["method"] == "fallback_peak_ratio"


def test_player_spike_no_fire_below_absolute_threshold():
    e = _entity(current_player_count=5_000, peak_in_game=10_000)
    assert PlayerSpikeDetector().detect(e) is None


# ----------------------------------------------------------------
# New release spike
# ----------------------------------------------------------------


def test_new_release_spike_top_seller():
    e = _entity(
        game_age_days=3,
        top_seller_rank=10,
        current_player_count=2_000,
    )
    r = NewReleaseSpikeDetector().detect(e)
    assert r is not None
    assert r.signal_type == "new_release_spike"


def test_new_release_spike_high_concurrent_alone():
    e = _entity(
        game_age_days=2,
        top_seller_rank=None,
        current_player_count=15_000,
    )
    r = NewReleaseSpikeDetector().detect(e)
    assert r is not None


def test_new_release_spike_old_game_not_fired():
    e = _entity(game_age_days=30, top_seller_rank=5, current_player_count=20_000)
    assert NewReleaseSpikeDetector().detect(e) is None


# ----------------------------------------------------------------
# Review surge
# ----------------------------------------------------------------


def test_review_surge_with_baseline():
    e = _entity(recent_24h_review_count=200)
    detector = ReviewSurgeDetector(baseline_provider=lambda _: 30.0)
    r = detector.detect(e)
    assert r is not None  # velocity ~6.7 > 3.0
    assert r.raw_data["method"] == "baseline"


def test_review_surge_fallback_lifetime_avg():
    # 365-day-old game with 365 lifetime reviews → 1/day avg.
    # 24h count of 50 → velocity 50, way above fallback threshold (5).
    e = _entity(
        recent_24h_review_count=50,
        total_reviews=365,
        game_age_days=365,
    )
    r = ReviewSurgeDetector().detect(e)
    assert r is not None
    assert r.raw_data["method"] == "fallback_lifetime_avg"


def test_review_surge_no_fire_on_low_volume():
    e = _entity(recent_24h_review_count=2)  # too few to bother
    assert ReviewSurgeDetector().detect(e) is None

"""Chart eligibility gate — decide whether a chart page has enough data to be useful.

Each function returns (eligible: bool, reason: str, fallback_type: str | None).
Thresholds come from tuning.viz so they can be tuned without code changes.

Fallback contract:
  rate_trend    not eligible → "stats_summary_card"  (key numbers as text)
  theme_share   not eligible → "risk_summary_card"   (buy_rec.key_risks as text)
  playtime_dist not eligible → None                  (page dropped entirely)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from xhs_agent.config.tuning import tuning

if TYPE_CHECKING:
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.domain.games.entity import GameEntity
    from xhs_agent.processors.playtime_buckets import PlaytimeBucketResult


def check_rate_trend(entity: "GameEntity") -> tuple[bool, str, Optional[str]]:
    """Eligible if both hist and 7d rates exist and differ by ≥ min_rate_diff."""
    p = tuning.viz.rate_chart
    hist = entity.historical_positive_rate
    recent = entity.recent_7d_positive_rate

    if hist is None:
        return False, "missing_historical_rate", "stats_summary_card"
    if recent is None:
        return False, "missing_recent_rate", "stats_summary_card"
    if entity.recent_7d_review_count is not None and entity.recent_7d_review_count < 10:
        return False, f"too_few_recent_reviews_{entity.recent_7d_review_count}", "stats_summary_card"

    diff = abs(hist - recent)
    if diff < p.min_rate_diff:
        return False, f"rate_diff_too_small_{diff:.3f}", "stats_summary_card"

    return True, f"rate_diff_{diff:.3f}", None


def check_theme_share(
    theme_summary: Optional["ThemeSummary"],
    entity: "GameEntity",
) -> tuple[bool, str, Optional[str]]:
    """Eligible if neg reviews ≥ min AND top theme share ≥ 25% AND top-3 ≥ 50%."""
    p = tuning.viz.theme_chart

    if theme_summary is None or not theme_summary.success or not theme_summary.themes:
        return False, "no_theme_data", "risk_summary_card"

    # Estimate negative review count from total analyzed and shares
    total_neg = sum(t.negative_count for t in theme_summary.themes)
    if total_neg < p.min_negative_reviews:
        return False, f"too_few_neg_reviews_{total_neg}", "risk_summary_card"

    # Check that top theme has a dominant share
    sorted_themes = sorted(theme_summary.themes, key=lambda t: -t.share_pct)
    top_share = sorted_themes[0].share_pct / 100 if sorted_themes else 0.0
    if top_share < p.min_top_theme_share:
        return False, f"no_dominant_theme_{top_share:.2f}", "risk_summary_card"

    # Check top-3 combined coverage
    top3_share = sum(t.share_pct for t in sorted_themes[:3]) / 100
    if top3_share < p.min_top3_total_share:
        return False, f"top3_too_diffuse_{top3_share:.2f}", "risk_summary_card"

    return True, f"top_theme_{sorted_themes[0].theme}_{top_share:.2f}", None


def check_price_history(entity: "GameEntity") -> tuple[bool, str, None]:
    """Eligible if the game is on sale and has ≥2 price change events."""
    if not getattr(entity, "is_on_special", False):
        return False, "not_on_sale", None
    history = getattr(entity, "price_history", None) or []
    discount_events = [e for e in history if e.get("cut", 0) > 0]
    if len(discount_events) < 1:
        return False, f"no_discount_events_in_history", None
    if len(history) < 2:
        return False, f"too_few_price_events_{len(history)}", None
    return True, f"price_events_{len(history)}_discounts_{len(discount_events)}", None


def check_language_region_gap(entity: "GameEntity") -> tuple[bool, str, None]:
    """Eligible only when there's a *meaningful* spread between regions' positive rates.

    We don't want to show this chart for every game — only when it tells a story
    (e.g. "国区好评率 80%，但俄语区只有 35%"). Requires ≥2 languages with enough
    sample size, AND a gap of ≥15 percentage points between max and min.
    """
    rates: dict = getattr(entity, "review_positive_rate_by_language", None) or {}
    if len(rates) < 2:
        return False, f"too_few_languages_{len(rates)}", None
    spread = (max(rates.values()) - min(rates.values())) * 100
    _MIN_GAP = 15
    if spread < _MIN_GAP:
        return False, f"gap_too_small_{spread:.0f}pp", None
    return True, f"gap_{spread:.0f}pp", None


def check_similar_games(entity: "GameEntity") -> tuple[bool, str, None]:
    """Eligible if we have ≥3 peer games with review data."""
    peers = getattr(entity, "similar_games", None) or []
    target_rate = getattr(entity, "historical_positive_rate", None)
    if target_rate is None:
        return False, "no_target_positive_rate", None
    if len(peers) < 3:
        return False, f"too_few_peers_{len(peers)}", None
    return True, f"peers_{len(peers)}", None


def check_player_history(entity: "GameEntity") -> tuple[bool, str, None]:
    """Eligible if ≥6 months of history and a meaningful recent trend exists.

    Uses recent 3-month vs previous 3-month comparison (not vs all-time peak)
    to avoid mislabeling stable mature games as "大量流失".
    """
    history = getattr(entity, "player_count_history", None) or []
    if len(history) < 6:
        return False, f"too_few_months_{len(history)}", None

    peaks = [r["peak"] for r in history]

    # Recent trend: last 3 months vs previous 3 months
    recent_avg = sum(peaks[-3:]) / 3
    prev_avg = sum(peaks[-6:-3]) / 3

    if prev_avg > 0:
        recent_change = (recent_avg - prev_avg) / prev_avg
        if recent_change <= -0.20:
            return True, f"player_declining_{recent_change:.0%}", None
        if recent_change >= 0.15:
            return True, f"player_growing_{recent_change:.0%}", None

    return False, "no_meaningful_recent_trend", None


def check_playtime_dist(
    buckets: Optional["PlaytimeBucketResult"],
) -> tuple[bool, str, Optional[str]]:
    """Eligible if total ≥ min AND (short_neg_share ≥ 0.30 OR long_pos_share ≥ 0.20)."""
    p = tuning.viz.playtime_chart

    if buckets is None or buckets.total == 0:
        return False, "no_playtime_data", None

    if buckets.total < p.min_reviews_with_playtime:
        return False, f"too_few_playtime_reviews_{buckets.total}", None

    short_neg_share = buckets.short_neg / buckets.total
    long_pos_share = buckets.long_pos / buckets.total

    has_contrast = (
        short_neg_share >= p.min_short_neg_share
        or long_pos_share >= p.min_long_pos_share
    )
    if not has_contrast:
        return (
            False,
            f"no_clear_contrast_short_{short_neg_share:.2f}_longpos_{long_pos_share:.2f}",
            None,
        )

    return True, f"contrast_short_{short_neg_share:.2f}_longpos_{long_pos_share:.2f}", None

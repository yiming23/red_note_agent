"""Tuning loader — reads tuning.yaml into typed Pydantic models.

Single source of truth for parameter knobs (thresholds, strictness, etc.).

Usage:
    from xhs_agent.config.tuning import tuning

    if entity.recent_24h_review_count > tuning.games.signals.review_surge.velocity_threshold:
        ...

Hot reload: cached on first read. Restart the process (scheduler/bot) to pick up
YAML changes. For testing or interactive shells, call `reload_tuning()`.

If the YAML is missing or malformed, defaults bake in here are used — same
values as the original Python constants, so behavior is unchanged when the
YAML is absent.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

# Use stdlib logging here — tuning is loaded before structlog can be configured
# (settings → tuning → ... → logger, a cycle if we import get_logger at module level).
_log = logging.getLogger(__name__)

TUNING_PATH = Path(__file__).resolve().parent / "tuning.yaml"


# ============================================================
# Pydantic models — defaults match the original Python constants.
# ============================================================


class NegativeBurstParams(BaseModel):
    drop_threshold: float = 0.15
    drop_strong_threshold: float = 0.30
    volume_threshold: int = 100
    volume_strong_threshold: int = 500


class PositiveBurstParams(BaseModel):
    rise_threshold: float = 0.10
    volume_threshold: int = 200
    min_age_days: int = 180


class PlayerSpikeParams(BaseModel):
    absolute_threshold: int = 10_000
    velocity_threshold: float = 2.0
    velocity_strong: float = 5.0
    fallback_ratio_threshold: float = 0.5
    # S5: fallback only triggers for games younger than this (avoids perma-giants like CS2)
    max_age_for_fallback_days: int = 90


class DiscountEventParams(BaseModel):
    min_discount_pct: int = 20
    urgent_discount_pct: int = 50
    min_reviews: int = 50


class HiddenGemParams(BaseModel):
    min_reviews: int = 100
    max_reviews: int = 5000
    min_positive_rate: float = 0.85
    min_age_days: int = 180


class NewReleaseSpikeParams(BaseModel):
    max_age_days: int = 7
    top_seller_rank_cutoff: int = 20
    player_threshold: int = 5_000


class ReviewSurgeParams(BaseModel):
    velocity_threshold: float = 3.0
    fallback_velocity_threshold: float = 5.0


class PlaytimeSplitParams(BaseModel):
    min_reviews_in_pool: int = 15
    short_neg_share_threshold: float = 0.30
    long_pos_share_threshold: float = 0.20


class GameSignalsParams(BaseModel):
    negative_burst: NegativeBurstParams = Field(default_factory=NegativeBurstParams)
    positive_burst: PositiveBurstParams = Field(default_factory=PositiveBurstParams)
    player_spike: PlayerSpikeParams = Field(default_factory=PlayerSpikeParams)
    new_release_spike: NewReleaseSpikeParams = Field(default_factory=NewReleaseSpikeParams)
    review_surge: ReviewSurgeParams = Field(default_factory=ReviewSurgeParams)
    discount_event: DiscountEventParams = Field(default_factory=DiscountEventParams)
    hidden_gem: HiddenGemParams = Field(default_factory=HiddenGemParams)
    playtime_split: PlaytimeSplitParams = Field(default_factory=PlaytimeSplitParams)


class SteamCollectorParams(BaseModel):
    default_limit: int = 30
    request_interval_sec: float = 0.5
    language: str = "schinese"


class SteamSpyCollectorParams(BaseModel):
    request_interval_sec: float = 1.0


class GameCollectorsParams(BaseModel):
    steam: SteamCollectorParams = Field(default_factory=SteamCollectorParams)
    steamspy: SteamSpyCollectorParams = Field(default_factory=SteamSpyCollectorParams)


class GamesDomainParams(BaseModel):
    signals: GameSignalsParams = Field(default_factory=GameSignalsParams)
    collectors: GameCollectorsParams = Field(default_factory=GameCollectorsParams)


class JudgmentParams(BaseModel):
    strictness: str = "balanced"  # strict / balanced / lenient
    force_approve_when_zero: int = 2


class ContentParams(BaseModel):
    max_candidates_per_run: int = 3
    active_memes_top_k: int = 5
    exemplars_per_template: int = 3


class PathsParams(BaseModel):
    exemplar_screenshots: str = "./exemplar_screenshots"
    data: str = "./data"
    fonts: str = "./assets/fonts"


# ============================================================
# v5 — Hashtag policy
# ============================================================


class HashtagPolicy(BaseModel):
    fixed: list[str] = Field(default_factory=lambda: [
        "#Steam游戏",
        "#真实玩家评论",
        "#游戏值不值得买",
        "#游戏评论研究所",
    ])
    by_content_type: dict[str, list[str]] = Field(default_factory=dict)
    genre_translations: dict[str, str] = Field(default_factory=dict)
    total_min: int = 5
    total_max: int = 10


# ============================================================
# v5 — Compliance
# ============================================================


class TitleRules(BaseModel):
    must_contain_brackets: bool = True
    min_chars: int = 16
    max_chars: int = 30


class ReviewQuoteRules(BaseModel):
    max_chars_per_quote: int = 100
    max_quotes_per_post: int = 5


class ComplianceParams(BaseModel):
    banned_phrases_to_rewrite: dict[str, str] = Field(default_factory=dict)
    title: TitleRules = Field(default_factory=TitleRules)
    review_quote: ReviewQuoteRules = Field(default_factory=ReviewQuoteRules)


# ============================================================
# v5 — Playtime buckets
# ============================================================


class PlaytimeBuckets(BaseModel):
    short_hours_max: int = 2
    long_hours_min: int = 20


# ============================================================
# Visualization eligibility thresholds (P0 upgrade)
# ============================================================


class RateChartParams(BaseModel):
    min_rate_diff: float = 0.05          # absolute diff between hist and 7d rates


class ThemeChartParams(BaseModel):
    min_negative_reviews: int = 30       # neg reviews needed to draw theme bar
    min_top_theme_share: float = 0.25    # top theme must cover ≥ 25% of analyzed
    min_top3_total_share: float = 0.50   # top-3 themes combined ≥ 50%


class PlaytimeChartParams(BaseModel):
    min_reviews_with_playtime: int = 50
    min_short_neg_share: float = 0.30    # short_neg / total ≥ threshold
    min_long_pos_share: float = 0.20     # OR long_pos / total ≥ threshold


class VizParams(BaseModel):
    rate_chart: RateChartParams = Field(default_factory=RateChartParams)
    theme_chart: ThemeChartParams = Field(default_factory=ThemeChartParams)
    playtime_chart: PlaytimeChartParams = Field(default_factory=PlaytimeChartParams)


# ============================================================
# Candidate selection quality gate
# ============================================================


class CandidateSelectionParams(BaseModel):
    min_review_count_7d_for_standard_candidate: int = 50
    max_prelaunch_candidates_per_run: int = 1
    force_pick_min_review_count_7d: int = 50


class Tuning(BaseModel):
    current_domain: str = "games"
    judgment: JudgmentParams = Field(default_factory=JudgmentParams)
    content: ContentParams = Field(default_factory=ContentParams)
    games: GamesDomainParams = Field(default_factory=GamesDomainParams)
    paths: PathsParams = Field(default_factory=PathsParams)

    # v5 additions
    hashtag: HashtagPolicy = Field(default_factory=HashtagPolicy)
    compliance: ComplianceParams = Field(default_factory=ComplianceParams)
    viz: VizParams = Field(default_factory=VizParams)
    candidate_selection: CandidateSelectionParams = Field(default_factory=CandidateSelectionParams)
    review_themes: list[str] = Field(default_factory=lambda: [
        "性能/优化", "服务器/联机", "Bug/崩溃", "价格/内容量",
        "玩法/手感", "剧情/美术", "平衡性", "中文/本地化",
        "DLC/商业化", "反作弊/账号", "新手引导/UI", "后期重复",
        "更新方向", "其他",
    ])
    playtime_buckets: PlaytimeBuckets = Field(default_factory=PlaytimeBuckets)


# ============================================================
# Loader
# ============================================================


@lru_cache(maxsize=1)
def get_tuning() -> Tuning:
    if not TUNING_PATH.exists():
        _log.warning("tuning.yaml missing at %s — using defaults", TUNING_PATH)
        return Tuning()
    try:
        with TUNING_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return Tuning(**data)
    except Exception as e:
        _log.error(
            "tuning.yaml parse failed: %s — using defaults with partial overrides", e
        )
        return Tuning()


def reload_tuning() -> Tuning:
    """Force a re-read from disk. Use after editing YAML in a running shell."""
    get_tuning.cache_clear()
    return get_tuning()


# Module-level singleton for ergonomic access
tuning: Tuning = get_tuning()

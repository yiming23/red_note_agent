"""GameEntity — the domain object for the games pack.

A snapshot of one game's relevant data at the time of collection. Signal detectors
consume these to decide if a signal triggers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class GameEntity:
    """Snapshot of a Steam game's signal-relevant data."""

    appid: str
    name: str

    # Concurrent players
    current_player_count: Optional[int] = None
    peak_in_game: Optional[int] = None              # all-time peak (Steam's own number)

    # Reviews — lifetime
    total_reviews: Optional[int] = None
    total_positive: Optional[int] = None
    historical_positive_rate: Optional[float] = None  # 0.0 - 1.0

    # Reviews — recent window (last N reviews from filter=recent)
    recent_reviews_count: Optional[int] = None       # how many recent reviews we sampled
    recent_positive_rate: Optional[float] = None     # 0.0 - 1.0
    recent_24h_review_count: Optional[int] = None    # reviews in last 24h
    recent_7d_review_count: Optional[int] = None     # reviews in last 7d
    recent_7d_positive_rate: Optional[float] = None  # 0.0 - 1.0 over 7d window

    # Top sellers / new releases / specials context
    is_top_seller: bool = False
    top_seller_rank: Optional[int] = None
    is_new_release: bool = False
    is_on_special: bool = False        # 当前是否在 Steam 特惠/折扣中
    discount_pct: Optional[int] = None  # 折扣百分比，e.g. 33 = 33% off
    original_price: Optional[float] = None  # 原价（USD）
    final_price: Optional[float] = None    # 折后价（USD）

    # Price history (from IsThereAnyDeal — populated only for discounted games when ITAD key configured)
    historic_low_price: Optional[float] = None   # 有记录以来 Steam 史低（USD）
    pct_above_historic_low: Optional[float] = None  # current vs historic low, e.g. 5.0 = 5% above low
    is_at_historic_low: bool = False             # True when within 5% of historic low
    # Each dict: {"date": "2026-04", "price": 6.24, "regular": 24.99, "cut": 75}
    price_history: list[dict] = field(default_factory=list)

    # Similar games comparison (from SteamSpy genre data)
    # Each dict: {"name": str, "appid": str, "positive_rate": float, "total_reviews": int}
    similar_games: list[dict] = field(default_factory=list)

    # Player count history (from SteamCharts — always populated)
    # Each dict: {"month": "2024-09", "peak": 12345, "avg": 8000}
    player_count_history: list[dict] = field(default_factory=list)
    player_count_all_time_peak: Optional[int] = None   # highest monthly peak ever
    player_count_peak_month: Optional[str] = None      # "2021-03"
    player_count_trend_pct: Optional[float] = None     # (current - peak) / peak * 100, negative = declined

    # Game metadata
    release_date_iso: Optional[str] = None           # "2024-09-20"
    game_age_days: Optional[int] = None
    is_free: bool = False
    genres: list[str] = field(default_factory=list)  # ["Indie", "Strategy"] — Steam genre descriptions
    short_description: Optional[str] = None          # Steam 商店页一句话简介，用于 LLM 上下文

    # Raw URLs / debug
    store_url: Optional[str] = None
    review_summary_url: Optional[str] = None

    # Direct-quote pool — 3-5 best snippets the content_agent can drop into prose verbatim
    sample_recent_review_excerpts: list[str] = field(default_factory=list)

    # LLM analysis pool — up to 30 reviews WITH text, fed to Review Miner for theme stats.
    # Each dict: {"text": str (≤200 chars), "voted_up": bool, "playtime_minutes": int|None}
    recent_review_pool: list[dict] = field(default_factory=list)

    # Stats pool — ALL fetched reviews (up to 100), metadata only, NO text.
    # Used for playtime distribution, language distribution, etc. without LLM cost.
    # Each dict: {"voted_up": bool, "playtime_minutes": int|None, "language": str}
    review_stats_pool: list[dict] = field(default_factory=list)

    # Language distribution derived from review_stats_pool (populated in steam.py)
    # e.g. {"schinese": 42, "english": 35, "tchinese": 8}
    review_language_dist: dict = field(default_factory=dict)
    chinese_review_pct: Optional[float] = None  # (schinese + tchinese) / total

    # External community opinions (from DuckDuckGoCollector, RedditClient, etc.)
    # external_opinions holds raw snippets; key_viewpoints holds LLM-processed viewpoints
    external_opinions: list = field(default_factory=list)  # list[ExternalOpinion]
    key_viewpoints: list = field(default_factory=list)      # list[KeyViewpoint]

    # When we collected this snapshot
    collected_at: datetime = field(default_factory=datetime.utcnow)

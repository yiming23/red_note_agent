"""ORM models. Single source of truth for database schema.

Schema changes MUST go through alembic migration. Do NOT call create_all in production code.
See DESIGN.md § 5 for table-by-table semantics.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from xhs_agent.storage.db import Base

# ============================================================
# Enums
# ============================================================


class PostState(str, enum.Enum):
    """Telegram review lifecycle for a generated candidate."""

    PENDING = "pending"        # 刚推送，等待审核
    ITERATING = "iterating"    # 收到反馈，正在重写
    APPROVED = "approved"      # ✅ 发布
    REJECTED = "rejected"      # ❌ 跳过
    PUBLISHED = "published"    # 你已经在小红书 app 发出去了（手动 log）
    FAILED = "failed"          # 重写连续失败


class SignalType(str, enum.Enum):
    NEGATIVE_BURST = "negative_burst"
    POSITIVE_BURST = "positive_burst"
    PLAYER_SPIKE = "player_spike"
    NEW_RELEASE_SPIKE = "new_release_spike"
    REVIEW_SURGE = "review_surge"
    DISCOUNT_EVENT = "discount_event"
    HIDDEN_GEM = "hidden_gem"
    PLAYTIME_SPLIT = "playtime_split"


class Severity(str, enum.Enum):
    URGENT = "urgent"
    NORMAL = "normal"
    LOW = "low"


class WatchStatus(str, enum.Enum):
    ACTIVE = "active"   # 持续有信号，每次 pipeline 处理
    WATCH = "watch"     # 降级观察池，低频拉取
    DEAD = "dead"       # 移除（保留记录用于历史回溯）


class MemeStatus(str, enum.Enum):
    RISING = "rising"
    PEAK = "peak"
    DECAYING = "decaying"
    DEAD = "dead"


class Language(str, enum.Enum):
    ZH = "zh"
    EN = "en"


class ExemplarSource(str, enum.Enum):
    XIAOHONGSHU = "xiaohongshu"
    WEIBO = "weibo"
    BILIBILI = "bilibili"
    MANUAL = "manual"


class PipelineType(str, enum.Enum):
    TREND = "trend"
    CONTENT = "content"


class PipelineStatus(str, enum.Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class LlmPurpose(str, enum.Enum):
    SIGNAL_JUDGMENT = "signal_judgment"
    TREND_EXTRACTION = "trend_extraction"
    REVIEW_MINING = "review_mining"
    OPINION_MINING = "opinion_mining"
    BUY_OR_WAIT = "buy_or_wait"
    TRANSLATION = "translation"
    COPYWRITING = "copywriting"
    REWRITE = "rewrite"
    EXEMPLAR_TAGGING = "exemplar_tagging"


# ============================================================
# Models
# ============================================================


class Post(Base):
    """A generated content candidate, from creation through review to (optional) publish."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Content
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_paths: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    hashtags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    template_used: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Signal source
    domain: Mapped[str] = mapped_column(String(32), nullable=False, default="games")
    trigger_entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trigger_entity_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    trigger_signals: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    source_urls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Prompt provenance
    prompt_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    used_meme_phrases: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    used_exemplar_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Telegram review state
    state: Mapped[PostState] = mapped_column(
        Enum(PostState), nullable=False, default=PostState.PENDING, index=True
    )
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    review_iterations: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True,
        doc="[{feedback, revised_content, revised_at}]",
    )

    # Generation metadata
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    llm_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pipeline_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True
    )

    # v5 multi-page / consumer-rating fields (S6)
    content_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    pages: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)         # list[PageDict]
    cover_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)     # page 1 hook text
    comment_prompt: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    buy_rating: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)  # A/B/C/D/E
    suitable_for: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    not_suitable_for: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    key_risks: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    wait_for: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    themes_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {theme: share_pct}
    playtime_buckets_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Post-publish (manual fill via scripts/log_post_result.py)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    edit_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    views_24h: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    likes_24h: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    saves_24h: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments_24h: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    views_7d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    likes_7d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    saves_7d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments_7d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    engagement_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class GameSignal(Base):
    """Snapshot of a detected game signal at a point in time."""

    __tablename__ = "game_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    game_name: Mapped[str] = mapped_column(String(256), nullable=False)
    signal_type: Mapped[SignalType] = mapped_column(Enum(SignalType), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[Severity] = mapped_column(Enum(Severity), nullable=False)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )


class Watchlist(Base):
    """Dynamic monitoring pool. One row per appid being watched."""

    __tablename__ = "watchlist"

    appid: Mapped[str] = mapped_column(String(32), primary_key=True)
    game_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[WatchStatus] = mapped_column(
        Enum(WatchStatus), nullable=False, default=WatchStatus.ACTIVE
    )
    last_signal_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_pulled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    consecutive_no_signal_count: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class MemePhrase(Base):
    """Auto-discovered meme/style phrases with lifecycle state."""

    __tablename__ = "meme_phrases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phrase: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    language: Mapped[Language] = mapped_column(Enum(Language), nullable=False)
    source_platform: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    frequency_score: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[MemeStatus] = mapped_column(
        Enum(MemeStatus), nullable=False, default=MemeStatus.RISING, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ExemplarPost(Base):
    """Reference scrolls — successful posts to use as few-shot examples."""

    __tablename__ = "exemplar_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_platform: Mapped[ExemplarSource] = mapped_column(
        Enum(ExemplarSource), nullable=False, default=ExemplarSource.MANUAL
    )
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    domain: Mapped[str] = mapped_column(String(32), nullable=False, default="games")
    template_match: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    style_tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    engagement_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    added_by: Mapped[str] = mapped_column(String(32), default="yiming")


class PromptVersion(Base):
    """Versioned prompt templates. Posts reference the version they were built with."""

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class PipelineRun(Base):
    """One row per pipeline execution (trend or content)."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_type: Mapped[PipelineType] = mapped_column(Enum(PipelineType), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[Optional[PipelineStatus]] = mapped_column(Enum(PipelineStatus), nullable=True)
    posts_generated: Mapped[int] = mapped_column(Integer, default=0)
    total_llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class ReviewThemeStat(Base):
    """Aggregated theme stats for one appid from one pipeline run.

    One row per (appid, theme, pipeline_run_id) triple. The Review Miner writes
    these after each batch classification; pipeline reads back the latest within 24h
    to skip re-running Haiku.
    """

    __tablename__ = "review_theme_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    pipeline_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True
    )
    theme: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, default=0)
    positive_count: Mapped[int] = mapped_column(Integer, default=0)
    share_pct: Mapped[int] = mapped_column(Integer, default=0)
    sample_quote: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    total_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )


class DailyReviewStats(Base):
    """Daily review aggregation snapshot for one appid.

    Written by daily_aggregator cron. Drives the positive-rate timeline chart (S7).
    """

    __tablename__ = "daily_review_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # "YYYY-MM-DD"
    pos_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    neg_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_pos_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rolling_7d_pos_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PriceSnapshot(Base):
    """Historic-low price snapshot from IsThereAnyDeal.

    One row per appid per pipeline run. Drives the 折扣值不值 price chart (S8).
    """

    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appid: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    pipeline_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True, index=True
    )
    current_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # final_price at collection time
    historic_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)    # all-time Steam low
    pct_above_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # (current - low) / low * 100
    is_at_historic_low: Mapped[bool] = mapped_column(Boolean, default=False)
    discount_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class LlmCall(Base):
    """One row per LLM API call. Drives cost tracking and debugging."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True, index=True
    )
    purpose: Mapped[LlmPurpose] = mapped_column(Enum(LlmPurpose), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

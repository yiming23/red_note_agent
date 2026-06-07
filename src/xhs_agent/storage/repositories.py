"""Repository layer — the only sanctioned way for business code to talk to the DB.

DESIGN.md § 14 invariant: "数据库只通过 repositories 访问".

Each repository wraps a model and exposes intention-revealing methods. Sessions
are passed in by the caller (typically via session_scope()) so transactions
compose properly across multiple repos.

This is V0 stub — methods will be filled in as modules need them. Adding a method
here is cheap; the goal is to keep the abstraction in place from day one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from xhs_agent.storage.models import (
    DailyReviewStats,
    ExemplarPost,
    GameSignal,
    LlmCall,
    MemePhrase,
    MemeStatus,
    PipelineRun,
    PipelineStatus,
    PipelineType,
    Post,
    PostState,
    PriceSnapshot,
    PromptVersion,
    ReviewThemeStat,
    Watchlist,
)


# ============================================================
# Posts
# ============================================================


class PostRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **fields) -> Post:
        post = Post(**fields)
        self.session.add(post)
        self.session.flush()  # populate post.id without committing
        return post

    def get(self, post_id: int) -> Optional[Post]:
        return self.session.get(Post, post_id)

    def get_latest_pending(self) -> Optional[Post]:
        """Most recent post in pending or iterating state — default reply target."""
        stmt = (
            select(Post)
            .where(Post.state.in_([PostState.PENDING, PostState.ITERATING]))
            .order_by(Post.generated_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def get_by_telegram_message_id(self, message_id: int) -> Optional[Post]:
        stmt = select(Post).where(Post.telegram_message_id == message_id)
        return self.session.scalars(stmt).first()

    def update_state(self, post_id: int, state: PostState) -> None:
        post = self.get(post_id)
        if post:
            post.state = state

    def recently_pushed_appids(self, days: int = 30) -> set[str]:
        """Appids of games we've generated content for (and didn't reject) within `days`.

        Used to avoid recommending the same game again too soon — keeps content fresh.
        REJECTED posts don't count (we explicitly chose not to run with that candidate).
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(Post.trigger_entity_id)
            .where(
                Post.generated_at >= cutoff,
                Post.trigger_entity_id.is_not(None),
                Post.state != PostState.REJECTED,
            )
            .distinct()
        )
        return {row[0] for row in self.session.execute(stmt).all() if row[0]}


# ============================================================
# Game signals + watchlist
# ============================================================


class GameSignalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, **fields) -> GameSignal:
        sig = GameSignal(**fields)
        self.session.add(sig)
        self.session.flush()
        return sig

    def recent_for_game(self, appid: str, hours: int = 24) -> Sequence[GameSignal]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stmt = (
            select(GameSignal)
            .where(GameSignal.appid == appid, GameSignal.detected_at >= cutoff)
            .order_by(GameSignal.detected_at.desc())
        )
        return self.session.scalars(stmt).all()


class WatchlistRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, appid: str, game_name: str) -> Watchlist:
        existing = self.session.get(Watchlist, appid)
        if existing:
            return existing
        entry = Watchlist(appid=appid, game_name=game_name)
        self.session.add(entry)
        self.session.flush()
        return entry

    def all_active(self) -> Sequence[Watchlist]:
        from xhs_agent.storage.models import WatchStatus

        stmt = select(Watchlist).where(Watchlist.status == WatchStatus.ACTIVE)
        return self.session.scalars(stmt).all()


# ============================================================
# Memes
# ============================================================


class MemeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_phrase(self, phrase: str) -> Optional[MemePhrase]:
        normalized = phrase.strip().lower()
        stmt = select(MemePhrase).where(MemePhrase.phrase == normalized)
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        phrase: str,
        language: str,
        source_platform: str,
        notes: Optional[str] = None,
    ) -> MemePhrase:
        normalized = phrase.strip().lower()
        existing = self.get_by_phrase(normalized)
        if existing:
            existing.last_seen_at = datetime.utcnow()
            existing.occurrence_count += 1
            return existing
        meme = MemePhrase(
            phrase=normalized,
            language=language,
            source_platform=source_platform,
            notes=notes,
        )
        self.session.add(meme)
        self.session.flush()
        return meme

    def fetch_active(self, top_k: int = 5) -> Sequence[MemePhrase]:
        """For prompt injection — fetch top-k by frequency_score among rising/peak."""
        stmt = (
            select(MemePhrase)
            .where(MemePhrase.status.in_([MemeStatus.RISING, MemeStatus.PEAK]))
            .order_by(MemePhrase.frequency_score.desc())
            .limit(top_k)
        )
        return self.session.scalars(stmt).all()


# ============================================================
# Exemplars
# ============================================================


class ExemplarRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, **fields) -> ExemplarPost:
        ex = ExemplarPost(**fields)
        self.session.add(ex)
        self.session.flush()
        return ex

    def fetch_by_template(
        self, template_name: str, k: int = 3
    ) -> Sequence[ExemplarPost]:
        """Pick k exemplars matching this template, preferring less-used ones."""
        stmt = (
            select(ExemplarPost)
            .where(ExemplarPost.template_match == template_name)
            .order_by(ExemplarPost.used_count.asc(), ExemplarPost.added_at.desc())
            .limit(k)
        )
        results = self.session.scalars(stmt).all()
        for ex in results:
            ex.used_count += 1
        return results


# ============================================================
# Review theme stats
# ============================================================


class ReviewThemeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_summary(
        self,
        appid: str,
        themes: list,  # list[ThemeStat] from review_miner
        total_analyzed: int,
        pipeline_run_id: Optional[int] = None,
    ) -> None:
        """Persist a ThemeSummary to DB. Each theme = one row."""
        now = datetime.utcnow()
        for t in themes:
            row = ReviewThemeStat(
                appid=appid,
                pipeline_run_id=pipeline_run_id,
                theme=t.theme,
                count=t.count,
                negative_count=t.negative_count,
                positive_count=t.positive_count,
                share_pct=t.share_pct,
                sample_quote=t.sample_quote,
                total_analyzed=total_analyzed,
                analyzed_at=now,
            )
            self.session.add(row)
        self.session.flush()

    def get_recent_for_appid(
        self, appid: str, max_age_hours: int = 24
    ) -> Sequence[ReviewThemeStat]:
        """Return the latest set of theme rows for appid within max_age_hours."""
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        stmt = (
            select(ReviewThemeStat)
            .where(
                ReviewThemeStat.appid == appid,
                ReviewThemeStat.analyzed_at >= cutoff,
            )
            .order_by(ReviewThemeStat.analyzed_at.desc())
        )
        return self.session.scalars(stmt).all()


class DailyReviewStatsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, appid: str, date: str, **fields) -> DailyReviewStats:
        """Insert or update a daily stats row for (appid, date)."""
        stmt = select(DailyReviewStats).where(
            DailyReviewStats.appid == appid,
            DailyReviewStats.date == date,
        )
        existing = self.session.scalars(stmt).first()
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            return existing
        row = DailyReviewStats(appid=appid, date=date, **fields)
        self.session.add(row)
        self.session.flush()
        return row

    def get_for_appid(self, appid: str, days: int = 30) -> Sequence[DailyReviewStats]:
        from datetime import date, timedelta as td

        cutoff = (date.today() - td(days=days)).isoformat()
        stmt = (
            select(DailyReviewStats)
            .where(DailyReviewStats.appid == appid, DailyReviewStats.date >= cutoff)
            .order_by(DailyReviewStats.date)
        )
        return self.session.scalars(stmt).all()


# ============================================================
# Prompt versions
# ============================================================


class PromptVersionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active(self, template_name: str) -> Optional[PromptVersion]:
        stmt = (
            select(PromptVersion)
            .where(
                PromptVersion.template_name == template_name,
                PromptVersion.is_active.is_(True),
            )
            .order_by(PromptVersion.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def create(self, **fields) -> PromptVersion:
        pv = PromptVersion(**fields)
        self.session.add(pv)
        self.session.flush()
        return pv


# ============================================================
# Pipeline runs / LLM calls
# ============================================================


class PipelineRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, pipeline_type: PipelineType) -> PipelineRun:
        run = PipelineRun(pipeline_type=pipeline_type)
        self.session.add(run)
        self.session.flush()
        return run

    def finish(
        self,
        run_id: int,
        status: PipelineStatus,
        posts_generated: int = 0,
        errors: Optional[list] = None,
    ) -> None:
        run = self.session.get(PipelineRun, run_id)
        if run:
            run.finished_at = datetime.utcnow()
            run.status = status
            run.posts_generated = posts_generated
            if errors:
                run.errors = errors


class PriceSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, *, appid: str, pipeline_run_id: Optional[int] = None,
               current_price: Optional[float] = None,
               historic_low: Optional[float] = None,
               pct_above_low: Optional[float] = None,
               is_at_historic_low: bool = False,
               discount_pct: Optional[int] = None) -> PriceSnapshot:
        snap = PriceSnapshot(
            appid=appid,
            pipeline_run_id=pipeline_run_id,
            current_price=current_price,
            historic_low=historic_low,
            pct_above_low=pct_above_low,
            is_at_historic_low=is_at_historic_low,
            discount_pct=discount_pct,
        )
        self.session.add(snap)
        self.session.flush()
        return snap


class LlmCallRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, **fields) -> LlmCall:
        call = LlmCall(**fields)
        self.session.add(call)
        self.session.flush()
        return call

    def total_cost_today(self) -> float:
        from sqlalchemy import func as sa_func

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(sa_func.coalesce(sa_func.sum(LlmCall.cost_usd), 0.0)).where(
            LlmCall.started_at >= today_start
        )
        return float(self.session.scalars(stmt).one())

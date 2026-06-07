"""Content pipeline — orchestrates collection → signal detection → LLM judgment → generation → push.

Top-level entry: `run_content_pipeline(domain=GAMES_DOMAIN, ...)`. This is what the
scheduler calls on a cron schedule, and what `scripts/run_pipeline_once.py` invokes
manually.

DESIGN.md § 3 data flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from xhs_agent.agents import (
    analyze_buy_or_wait,
    compliance_check,
    extract_themes,
    generate_content,
    judge_signals,
)
from xhs_agent.processors.daily_aggregator import aggregate_daily
from xhs_agent.processors.page_builder import build_pages
from xhs_agent.processors.playtime_buckets import compute_buckets
from xhs_agent.visualization import render_all_pages
from xhs_agent.budget.guard import BudgetExceededError
from xhs_agent.config import tuning
from xhs_agent.domain.base import Domain, SignalResult
from xhs_agent.domain.games import GAMES_DOMAIN, GameEntity
from xhs_agent.observability.logger import get_logger
from xhs_agent.publishers.telegram_push import send_candidate, send_candidate_with_images, send_plain
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import (
    PipelineStatus,
    PipelineType,
    PostState,
    Severity,
    SignalType,
)
from xhs_agent.storage.repositories import (
    GameSignalRepository,
    PipelineRunRepository,
    PostRepository,
    PriceSnapshotRepository,
    WatchlistRepository,
)
from xhs_agent.utils.formatter import format_post

log = get_logger(__name__)


@dataclass
class PipelineResult:
    run_id: int
    signals_detected: int
    signals_approved: int
    posts_generated: int
    posts_pushed: int
    errors: list[str]


def run_content_pipeline(
    domain: Domain = GAMES_DOMAIN,
    *,
    collect_limit: Optional[int] = None,
    max_candidates_per_run: Optional[int] = None,
    push_to_telegram: bool = True,
) -> PipelineResult:
    """End-to-end pipeline: collect → detect → judge → generate → push.

    Args:
        domain: which domain pack to run (V0: games)
        collect_limit: how many entities to pull from each collector
        max_candidates_per_run: cap on how many posts we generate per run
        push_to_telegram: set False for dry-run-without-push integration tests
    """
    if collect_limit is None:
        collect_limit = tuning.games.collectors.steam.default_limit
    if max_candidates_per_run is None:
        max_candidates_per_run = tuning.content.max_candidates_per_run
    log.info("pipeline_start", domain=domain.name, limit=collect_limit)

    errors: list[str] = []
    signals_detected = 0
    signals_approved = 0
    posts_generated = 0
    posts_pushed = 0

    with session_scope() as s:
        run = PipelineRunRepository(s).start(PipelineType.CONTENT)
        run_id = run.id

    try:
        # 1. Collect entities from each collector
        entities: list = []
        for collector in domain.collectors:
            try:
                batch = list(collector.collect(limit=collect_limit))
                entities.extend(batch)
                log.info("collector_done", name=collector.name, count=len(batch))
            except Exception as exc:
                log.error("collector_failed", name=collector.name, error=str(exc))
                errors.append(f"{collector.name}: {exc}")

        if not entities:
            _finish(run_id, PipelineStatus.FAILED, posts_generated, errors)
            return PipelineResult(run_id, 0, 0, 0, 0, errors)

        entities_by_id: dict[str, GameEntity] = {
            getattr(e, "appid", str(i)): e for i, e in enumerate(entities)
        }

        # 1b. Persist price snapshots for discounted games that have ITAD data
        discounted = [e for e in entities if getattr(e, "is_on_special", False) and getattr(e, "historic_low_price", None) is not None]
        if discounted:
            with session_scope() as s:
                pr = PriceSnapshotRepository(s)
                for e in discounted:
                    pr.record(
                        appid=e.appid,
                        pipeline_run_id=run_id,
                        current_price=e.final_price,
                        historic_low=e.historic_low_price,
                        pct_above_low=e.pct_above_historic_low,
                        is_at_historic_low=e.is_at_historic_low,
                        discount_pct=e.discount_pct,
                    )
            log.info("price_snapshots_saved", count=len(discounted))

        # 2. Run all detectors against all entities → flat SignalResult list
        signals: list[SignalResult] = []
        for entity in entities:
            for detector in domain.detectors:
                try:
                    result = detector.detect(entity)
                    if result is not None:
                        signals.append(result)
                except Exception as exc:
                    log.error(
                        "detector_failed",
                        detector=detector.signal_type,
                        entity=getattr(entity, "appid", "?"),
                        error=str(exc),
                    )

        signals_detected = len(signals)
        log.info("signals_detected", count=signals_detected)

        if not signals:
            log.info("pipeline_no_signals")
            _finish(run_id, PipelineStatus.SUCCESS, 0, errors)
            return PipelineResult(run_id, 0, 0, 0, 0, errors)

        # Persist signal records + update watchlist
        with session_scope() as s:
            sig_repo = GameSignalRepository(s)
            wl_repo = WatchlistRepository(s)
            for sig in signals:
                sig_repo.record(
                    appid=sig.entity_id,
                    game_name=sig.entity_name,
                    signal_type=SignalType(sig.signal_type),
                    score=sig.score,
                    severity=Severity(sig.severity),
                    raw_data=sig.raw_data,
                )
                wl_repo.upsert(appid=sig.entity_id, game_name=sig.entity_name)

        # 3. Sort by severity (urgent first), then score
        signals.sort(
            key=lambda x: (
                0 if x.severity == "urgent" else 1 if x.severity == "normal" else 2,
                -x.score,
            )
        )

        # 3b. Drop games we've already generated content for recently — keeps the
        #     feed fresh and avoids repeating the same game within a cooldown window.
        _cooldown_days = tuning.candidate_selection.recent_push_cooldown_days
        with session_scope() as s:
            recent_appids = PostRepository(s).recently_pushed_appids(days=_cooldown_days)
        if recent_appids:
            before = len(signals)
            signals = [sig for sig in signals if sig.entity_id not in recent_appids]
            log.info(
                "signals_filtered_recent_pushes",
                dropped=before - len(signals),
                remaining=len(signals),
                cooldown_days=_cooldown_days,
            )
        if not signals:
            log.info("pipeline_no_signals_after_cooldown_filter")
            _finish(run_id, PipelineStatus.SUCCESS, 0, errors)
            return PipelineResult(run_id, signals_detected, 0, 0, 0, errors)

        # 4. LLM judgment — Haiku decides what's actually worth writing
        # Send a window of top signals to keep prompt size sane
        top_signals = signals[: max(max_candidates_per_run * 3, 6)]
        judgments = judge_signals(top_signals, entities_by_id, pipeline_run_id=run_id)
        approved = [j for j in judgments if j.worth_writing]
        signals_approved = len(approved)
        log.info("judgments_done", approved=signals_approved)

        # 5. Enforce prelaunch cap: at most max_prelaunch low-review candidates;
        #    review-backed candidates always take priority.
        _min_r = tuning.candidate_selection.min_review_count_7d_for_standard_candidate
        _max_pre = tuning.candidate_selection.max_prelaunch_candidates_per_run

        def _review_7d(j) -> int:
            e = entities_by_id.get(j.signal.entity_id)
            return (getattr(e, "recent_7d_review_count", None) or 0) if e else 0

        review_backed = [j for j in approved if _review_7d(j) >= _min_r]
        prelaunch_cands = [j for j in approved if _review_7d(j) < _min_r]
        approved = review_backed + prelaunch_cands[:_max_pre]

        if prelaunch_cands and not review_backed:
            log.warning("no_review_backed_candidate",
                        prelaunch_count=len(prelaunch_cands),
                        kept=len(approved))

        # Cap to max candidates per run
        approved = approved[:max_candidates_per_run]

        # 6. Enrich selected candidates with per-candidate data (deferred to avoid
        #    calling external APIs for every game in the pool — only selected ones need it)
        from xhs_agent.domain.games.collectors.similar_games import SimilarGamesCollector
        from xhs_agent.domain.games.collectors.reddit import RedditClient
        from xhs_agent.domain.games.collectors.duckduckgo import DuckDuckGoCollector, build_ddg_queries
        from xhs_agent.agents.opinion_miner import mine_opinions

        # signal_type → article_template for DDG query generation
        _SIGNAL_TO_TEMPLATE: dict[str, str] = {
            "negative_burst":   "negative_review_burst",
            "positive_burst":   "comeback_game",
            "new_release_spike":"new_release_heat",
            "review_surge":     "new_release_heat",
            "discount_event":   "discount_worth_checking",
            "hidden_gem":       "hidden_gem",
            "playtime_split":   "playtime_contrast",
            "player_spike":     "new_release_heat",
        }

        from xhs_agent.domain.games.collectors.steam import SteamCollector

        _sg_collector = SimilarGamesCollector()
        _reddit_client = RedditClient()
        _ddg_client = DuckDuckGoCollector()
        _steam_collector = SteamCollector()
        for judgment in approved:
            entity = entities_by_id.get(judgment.signal.entity_id)
            if entity is None:
                continue
            # 6a0. Lifetime per-language positive rate (true totals, not a sample —
            #      only worth the extra API calls for games we're actually writing about)
            try:
                _steam_collector.enrich_language_breakdown(entity)
            except Exception as exc:
                log.warning("lifetime_lang_breakdown_failed", appid=entity.appid, error=str(exc))
            # 6a. Similar games
            if not getattr(entity, "similar_games", None):
                try:
                    _sg_collector.enrich_entity(entity)
                except Exception as exc:
                    log.warning("similar_games_enrich_failed",
                                appid=entity.appid, error=str(exc))
            # 6b. Reddit community opinions → DDG fallback
            if not getattr(entity, "external_opinions", None):
                try:
                    _reddit_client.enrich_entity(entity)
                except Exception as exc:
                    log.warning("reddit_enrich_failed",
                                appid=entity.appid, error=str(exc))

            # 6c. DDG fallback: if Reddit returned nothing, search DuckDuckGo
            if not getattr(entity, "external_opinions", None):
                try:
                    template = _SIGNAL_TO_TEMPLATE.get(
                        judgment.signal.signal_type, "new_release_heat"
                    )
                    queries = build_ddg_queries(entity.name, template)
                    opinions = _ddg_client.search(queries)
                    if opinions:
                        entity.external_opinions = opinions
                        log.info("ddg_fallback_used", appid=entity.appid,
                                 signal=judgment.signal.signal_type,
                                 opinions=len(opinions))
                except Exception as exc:
                    log.warning("ddg_fallback_failed",
                                appid=entity.appid, error=str(exc))

            # 6d. Mine opinions (Reddit or DDG, whichever populated)
            if getattr(entity, "external_opinions", None) and not getattr(entity, "key_viewpoints", None):
                try:
                    source_label = "Reddit" if any(
                        o.source == "reddit" for o in entity.external_opinions
                    ) else "DuckDuckGo"
                    entity.key_viewpoints = mine_opinions(
                        entity.external_opinions,
                        game_name=entity.name,
                        sources_label=source_label,
                        pipeline_run_id=run_id,
                    )
                except Exception as exc:
                    log.warning("opinion_mining_failed",
                                appid=entity.appid, error=str(exc))

        # 7. Generate content for each approved judgment
        for judgment in approved:
            entity = entities_by_id.get(judgment.signal.entity_id)
            if entity is None:
                continue

            # 7a. Review Miner — classify recent reviews into 14 fixed themes.
            #     Output drives the "约 X% 集中在 Y" percentages content_agent uses.
            theme_summary = None
            pool = getattr(entity, "recent_review_pool", None)
            if pool:
                try:
                    theme_summary = extract_themes(
                        pool=pool,
                        appid=entity.appid,
                        pipeline_run_id=run_id,
                        use_cache=True,
                    )
                except BudgetExceededError:
                    log.warning("review_miner_skipped_budget", appid=entity.appid)
                except Exception as exc:
                    log.warning("review_miner_failed", appid=entity.appid, error=str(exc))

            # Compute playtime buckets from the review pool (no LLM cost)
            buckets = compute_buckets(entity) if pool else None

            try:
                generated = generate_content(
                    judgment=judgment,
                    entity=entity,
                    domain=domain,
                    theme_summary=theme_summary,
                    playtime_buckets=buckets,
                    pipeline_run_id=run_id,
                )
            except BudgetExceededError as exc:
                log.error("pipeline_budget_blown", error=str(exc))
                errors.append("budget_exceeded")
                send_plain(f"⚠️ 今日 LLM 预算超限，pipeline 提前停止：{exc}")
                break
            except Exception as exc:
                log.error("content_generation_failed", error=str(exc))
                errors.append(f"content_agent: {exc}")
                continue

            if not generated.success:
                errors.append(f"content_agent_invalid_output: {generated.error_message}")
                continue

            # 7b. Buy-or-Wait Analyst
            buy_rec = None
            try:
                buy_rec = analyze_buy_or_wait(
                    entity=entity,
                    theme_summary=theme_summary,
                    playtime_buckets=buckets,
                    pipeline_run_id=run_id,
                )
            except BudgetExceededError:
                log.warning("buy_or_wait_skipped_budget", appid=entity.appid)
            except Exception as exc:
                log.warning("buy_or_wait_failed", appid=entity.appid, error=str(exc))

            posts_generated += 1

            # 8. Format + compliance check
            template_obj = next(
                (t for t in domain.templates if t.name == generated.template_name),
                None,
            )
            content_type_zh = template_obj.content_type if template_obj else generated.template_name
            formatted = format_post(
                title=generated.title,
                content=generated.content,
                hashtags=generated.hashtags,
                content_type=content_type_zh,
                game_name=entity.name,
                genres=list(entity.genres) if entity.genres else [],
            )

            # Compliance Guard — further checks on top of formatter
            clean_title, clean_content, compliance_report = compliance_check(
                title=formatted.title,
                content=formatted.content,
                hashtags=formatted.hashtags,
                game_name=entity.name,
                buy_rec=buy_rec,
            )
            if compliance_report.rewrites:
                formatted = format_post(
                    title=clean_title,
                    content=clean_content,
                    hashtags=formatted.hashtags,
                    content_type=content_type_zh,
                    game_name=entity.name,
                    genres=list(entity.genres) if entity.genres else [],
                )
            if compliance_report.blocks:
                log.warning(
                    "compliance_blocked",
                    appid=entity.appid,
                    blocks=compliance_report.blocks,
                )

            # 9. Build multi-page structure + render images
            pages = build_pages(
                title=formatted.title,
                content=formatted.content,
                entity=entity,
                buy_rec=buy_rec or _default_buy_rec(),
                theme_summary=theme_summary,
                playtime_buckets=buckets,
            )
            try:
                pages = render_all_pages(
                    entity=entity,
                    buy_rec=buy_rec or _default_buy_rec(),
                    pages=pages,
                    theme_summary=theme_summary,
                    playtime_buckets=buckets,
                    title=formatted.title,
                )
            except Exception as exc:
                log.warning("viz_render_failed", appid=entity.appid, error=str(exc))

            # 10. Persist Post with v5 fields
            total_cost = generated.cost_usd + (buy_rec.cost_usd if buy_rec else 0.0)
            themes_summary_dict = None
            if theme_summary and theme_summary.themes:
                themes_summary_dict = {t.theme: t.share_pct for t in theme_summary.themes}

            with session_scope() as s:
                post = PostRepository(s).create(
                    title=formatted.title,
                    content=formatted.content,
                    hashtags=formatted.hashtags,
                    template_used=generated.template_name,
                    domain=domain.name,
                    trigger_entity_id=judgment.signal.entity_id,
                    trigger_entity_name=judgment.signal.entity_name,
                    trigger_signals=[judgment.signal.signal_type],
                    source_urls=[entity.store_url, entity.review_summary_url] if entity.store_url else None,
                    used_meme_phrases=None,
                    used_exemplar_ids=None,
                    state=PostState.PENDING,
                    llm_cost_usd=total_cost,
                    pipeline_run_id=run_id,
                    # v5 fields
                    content_type=content_type_zh,
                    pages=pages,
                    cover_text=pages[0]["body"] if pages else None,
                    buy_rating=buy_rec.rating if buy_rec else None,
                    suitable_for=buy_rec.suitable_for if buy_rec else None,
                    not_suitable_for=buy_rec.not_suitable_for if buy_rec else None,
                    key_risks=buy_rec.key_risks if buy_rec else None,
                    wait_for=buy_rec.wait_for if buy_rec else None,
                    themes_summary=themes_summary_dict,
                    playtime_buckets_json=buckets.as_dict() if buckets else None,
                )
                post_id = post.id
                post_for_send = post
                s.expunge(post_for_send)

            signal_summary = _build_signal_summary(judgment, entity, buy_rec, compliance_report)
            if push_to_telegram:
                send_result = send_candidate_with_images(post_for_send, formatted, signal_summary, pages)
                if send_result.success and send_result.telegram_message_id:
                    posts_pushed += 1
                    with session_scope() as s:
                        post = PostRepository(s).get(post_id)
                        if post:
                            post.telegram_message_id = send_result.telegram_message_id
                else:
                    errors.append(f"telegram_push: {send_result.error}")

        # Aggregate daily review stats for all collected entities
        try:
            aggregate_daily(entities)
        except Exception as exc:
            log.warning("daily_aggregator_failed", error=str(exc))

        _finish(run_id, PipelineStatus.SUCCESS if not errors else PipelineStatus.PARTIAL,
                posts_generated, errors)

    except Exception as exc:
        log.error("pipeline_crashed", error=str(exc))
        errors.append(f"crash: {exc}")
        _finish(run_id, PipelineStatus.FAILED, posts_generated, errors)

    log.info(
        "pipeline_done",
        run_id=run_id,
        signals_detected=signals_detected,
        signals_approved=signals_approved,
        posts_generated=posts_generated,
        posts_pushed=posts_pushed,
        errors=len(errors),
    )

    return PipelineResult(
        run_id=run_id,
        signals_detected=signals_detected,
        signals_approved=signals_approved,
        posts_generated=posts_generated,
        posts_pushed=posts_pushed,
        errors=errors,
    )


# ============================================================
# Helpers
# ============================================================


def _build_signal_summary(
    judgment,
    entity: GameEntity,
    buy_rec=None,
    compliance_report=None,
) -> str:
    sig = judgment.signal
    parts = [
        f"📡 信号: {sig.signal_type} ({sig.severity})",
        f"🎯 角度: {judgment.angle or '(无)'}",
    ]
    if entity.recent_7d_review_count is not None:
        parts.append(
            f"📊 7d 评论: {entity.recent_7d_review_count} | "
            f"好评率: {entity.recent_7d_positive_rate} | "
            f"历史: {entity.historical_positive_rate}"
        )
    if entity.is_on_special and entity.discount_pct:
        parts.append(f"💰 折扣: 直降 {entity.discount_pct}% → ¥{entity.final_price}")
    if entity.current_player_count:
        parts.append(f"👥 当前在线: {entity.current_player_count}")
    if buy_rec and buy_rec.success:
        parts.append(buy_rec.format_for_telegram())
    if compliance_report and not compliance_report.passed:
        parts.append(f"⚠️ 合规: {' / '.join(compliance_report.blocks[:2])}")
    if entity.store_url:
        parts.append(f"🔗 {entity.store_url}")
    return "\n".join(parts)


def _default_buy_rec():
    from xhs_agent.agents.buy_or_wait import BuyRecommendation
    return BuyRecommendation(rating="C", one_sentence="数据不足，建议先观望", success=False)


def _finish(run_id: int, status: PipelineStatus, posts_generated: int, errors: list[str]) -> None:
    with session_scope() as s:
        PipelineRunRepository(s).finish(
            run_id=run_id,
            status=status,
            posts_generated=posts_generated,
            errors=errors or None,
        )

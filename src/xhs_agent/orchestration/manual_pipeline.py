"""Manual pipeline — triggered by Telegram /gen command.

Flow:
  1. Steam search → find appid for game_name
  2. Collect entity (SteamCollector for this one appid)
  3. ContentDirector (Haiku) → ContentPlan (template + charts + queries)
  4. DuckDuckGoCollector → entity.external_opinions
  5. OpinionMiner (Haiku) → entity.key_viewpoints
  6. ReviewMiner (Haiku, if pool available) → theme_summary
  7. BuyOrWait (Haiku) → buy_rec
  8. build_pages_from_plan() — bypasses eligibility gates
  9. ContentAgent (Sonnet) → title + content + hashtags
  10. Push to Telegram
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

import httpx

from xhs_agent.agents.buy_or_wait import BuyRecommendation, analyze as analyze_buy_or_wait
from xhs_agent.agents.compliance_guard import check as compliance_check
from xhs_agent.agents.content_agent import GeneratedContent, generate_content
from xhs_agent.agents.content_director import ContentPlan, direct_content
from xhs_agent.agents.opinion_miner import mine_opinions
from xhs_agent.agents.review_miner import extract_themes
from xhs_agent.agents.signal_agent import SignalJudgment
from xhs_agent.budget.guard import BudgetExceededError
from xhs_agent.config import settings
from xhs_agent.domain.base import SignalResult
from xhs_agent.domain.games import GAMES_DOMAIN, GameEntity
from xhs_agent.domain.games.collectors.duckduckgo import DuckDuckGoCollector
from xhs_agent.domain.games.collectors.similar_games import SimilarGamesCollector
from xhs_agent.domain.games.collectors.steam import SteamCollector
from xhs_agent.observability.logger import get_logger
from xhs_agent.processors.page_builder import build_pages_from_plan
from xhs_agent.processors.playtime_buckets import compute_buckets
from xhs_agent.publishers.telegram_push import send_candidate_with_images, send_plain
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import PostState
from xhs_agent.storage.repositories import PostRepository
from xhs_agent.utils.formatter import format_post
from xhs_agent.visualization import render_all_pages

log = get_logger(__name__)

_STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
_SEARCH_TIMEOUT = 10.0


def run_manual_pipeline(
    game_name: str,
    user_direction: str,
    external_article_url: Optional[str] = None,
    chat_id: Optional[int] = None,
    push_to_telegram: bool = True,
) -> None:
    """Manual article generation triggered by Telegram /gen command."""
    log.info("manual_pipeline_start", game=game_name, direction=user_direction[:60])

    try:
        # 1. Find appid
        appid = _lookup_appid(game_name)
        if not appid:
            _notify(f"❌ 找不到游戏《{game_name}》，请检查名字是否正确", chat_id, push_to_telegram)
            return

        # 2. Collect entity
        entity = _collect_entity(appid)
        if entity is None:
            _notify(f"❌ 数据获取失败（appid={appid}），请稍后重试", chat_id, push_to_telegram)
            return

        log.info("manual_entity_collected", appid=appid, name=entity.name)

        # 3. Fetch external article if URL provided
        external_article_text = None
        if external_article_url:
            try:
                external_article_text = _fetch_article_text(external_article_url)
            except Exception as exc:
                log.warning("manual_external_article_failed", url=external_article_url, error=str(exc))

        # 4. ContentDirector — decide article plan
        available_data = _summarize_available_data(entity)
        try:
            content_plan = direct_content(
                game_name=entity.name,
                user_direction=user_direction,
                available_data=available_data,
                external_article_text=external_article_text,
            )
        except Exception as exc:
            log.warning("manual_content_director_failed", error=str(exc))
            content_plan = ContentPlan(
                article_template="negative_review_burst",
                key_narrative=user_direction[:50],
                search_queries=[entity.name],
                charts_needed=[],
                buy_rec_context="",
            )

        log.info(
            "manual_content_plan",
            template=content_plan.article_template,
            narrative=content_plan.key_narrative,
            charts=content_plan.charts_needed,
        )

        # 5. DuckDuckGo search
        if content_plan.search_queries:
            try:
                ddg = DuckDuckGoCollector()
                entity.external_opinions = ddg.search(content_plan.search_queries)
                log.info("manual_ddg_done", count=len(entity.external_opinions))
            except Exception as exc:
                log.warning("manual_ddg_failed", error=str(exc))

        # 6. Opinion mining
        if entity.external_opinions:
            try:
                entity.key_viewpoints = mine_opinions(
                    entity.external_opinions,
                    game_name=entity.name,
                    sources_label="DuckDuckGo搜索",
                )
                entity._sources_label = "DuckDuckGo搜索"
            except Exception as exc:
                log.warning("manual_opinion_miner_failed", error=str(exc))

        # 7. Similar games (optional, skip on failure)
        if not entity.similar_games:
            try:
                SimilarGamesCollector().enrich_entity(entity)
            except Exception as exc:
                log.warning("manual_similar_games_failed", error=str(exc))

        # 8. Review Miner
        theme_summary = None
        if entity.recent_review_pool:
            try:
                theme_summary = extract_themes(
                    pool=entity.recent_review_pool,
                    appid=entity.appid,
                    use_cache=True,
                )
            except BudgetExceededError:
                log.warning("manual_review_miner_budget")
            except Exception as exc:
                log.warning("manual_review_miner_failed", error=str(exc))

        buckets = compute_buckets(entity) if entity.recent_review_pool else None

        # 9. Buy-or-Wait
        buy_rec = None
        try:
            buy_rec = analyze_buy_or_wait(
                entity=entity,
                theme_summary=theme_summary,
                playtime_buckets=buckets,
            )
        except BudgetExceededError:
            log.warning("manual_buy_or_wait_budget")
        except Exception as exc:
            log.warning("manual_buy_or_wait_failed", error=str(exc))

        if buy_rec is None:
            buy_rec = BuyRecommendation(
                rating="C",
                one_sentence="数据不足，建议先观望",
                success=False,
            )

        # 10. Content generation
        synthetic_judgment = _make_judgment(content_plan, entity)
        template_obj = next(
            (t for t in GAMES_DOMAIN.templates if t.name == content_plan.article_template),
            GAMES_DOMAIN.templates[0],
        )

        try:
            generated = generate_content(
                judgment=synthetic_judgment,
                entity=entity,
                domain=GAMES_DOMAIN,
                theme_summary=theme_summary,
                playtime_buckets=buckets,
            )
        except BudgetExceededError as exc:
            _notify(f"⚠️ 预算超限，生成中止：{exc}", chat_id, push_to_telegram)
            return
        except Exception as exc:
            log.error("manual_content_generation_failed", error=str(exc))
            _notify(f"❌ 文章生成失败：{exc}", chat_id, push_to_telegram)
            return

        if not generated.success:
            _notify(f"❌ 文章生成输出无效：{generated.error_message}", chat_id, push_to_telegram)
            return

        # 11. Format + compliance
        content_type_zh = template_obj.content_type
        formatted = format_post(
            title=generated.title,
            content=generated.content,
            hashtags=generated.hashtags,
            content_type=content_type_zh,
            game_name=entity.name,
            genres=list(entity.genres) if entity.genres else [],
        )

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

        # 12. Build pages (plan-driven, no eligibility gates)
        pages = build_pages_from_plan(
            title=formatted.title,
            content=formatted.content,
            entity=entity,
            buy_rec=buy_rec,
            content_plan=content_plan,
            theme_summary=theme_summary,
            playtime_buckets=buckets,
        )

        try:
            pages = render_all_pages(
                entity=entity,
                buy_rec=buy_rec,
                pages=pages,
                theme_summary=theme_summary,
                playtime_buckets=buckets,
                title=formatted.title,
            )
        except Exception as exc:
            log.warning("manual_viz_render_failed", error=str(exc))

        # 13. Persist post
        total_cost = generated.cost_usd + (buy_rec.cost_usd if hasattr(buy_rec, "cost_usd") else 0.0)
        themes_dict = None
        if theme_summary and theme_summary.themes:
            themes_dict = {t.theme: t.share_pct for t in theme_summary.themes}

        with session_scope() as s:
            post = PostRepository(s).create(
                title=formatted.title,
                content=formatted.content,
                hashtags=formatted.hashtags,
                template_used=generated.template_name,
                domain=GAMES_DOMAIN.name,
                trigger_entity_id=entity.appid,
                trigger_entity_name=entity.name,
                trigger_signals=["manual_gen"],
                source_urls=[entity.store_url] if entity.store_url else None,
                used_meme_phrases=None,
                used_exemplar_ids=None,
                state=PostState.PENDING,
                llm_cost_usd=total_cost,
                pipeline_run_id=None,
                content_type=content_type_zh,
                pages=pages,
                cover_text=pages[0]["body"] if pages else None,
                buy_rating=buy_rec.rating if buy_rec else None,
                suitable_for=buy_rec.suitable_for if buy_rec else None,
                not_suitable_for=buy_rec.not_suitable_for if buy_rec else None,
                key_risks=buy_rec.key_risks if buy_rec else None,
                wait_for=buy_rec.wait_for if buy_rec else None,
                themes_summary=themes_dict,
                playtime_buckets_json=buckets.as_dict() if buckets else None,
            )
            post_id = post.id
            post_for_send = post
            s.expunge(post_for_send)

        signal_summary = (
            f"🎮 手动生成：{entity.name}\n"
            f"📝 方向：{user_direction[:80]}\n"
            f"📐 模板：{content_type_zh}\n"
            f"💡 叙事：{content_plan.key_narrative}\n"
        )
        if buy_rec and buy_rec.success:
            signal_summary += buy_rec.format_for_telegram()

        if push_to_telegram:
            send_candidate_with_images(post_for_send, formatted, signal_summary, pages)

        log.info("manual_pipeline_done", post_id=post_id, game=entity.name)

    except Exception as exc:
        log.error("manual_pipeline_crashed", error=str(exc))
        _notify(f"❌ 手动生成崩溃：{exc}", chat_id, push_to_telegram)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def search_games(game_name: str, limit: int = 5) -> list[tuple[str, str]]:
    """Search Steam store, return up to `limit` (appid, name) tuples."""
    try:
        resp = httpx.get(
            _STEAM_SEARCH_URL,
            params={"term": game_name, "l": "schinese", "cc": "CN"},
            timeout=_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        results = [(str(it["id"]), it.get("name", f"app_{it['id']}")) for it in items[:limit]]
        log.info("steam_search_done", query=game_name, count=len(results))
        return results
    except Exception as exc:
        log.warning("steam_search_failed", query=game_name, error=str(exc))
    return []


def _lookup_appid(game_name: str) -> Optional[str]:
    results = search_games(game_name, limit=1)
    return results[0][0] if results else None


def _collect_entity(appid: str) -> Optional[GameEntity]:
    """Collect a full entity for a single appid using SteamCollector internals."""
    try:
        collector = SteamCollector()
        # Build a minimal entity then enrich it as the collector would
        entity = GameEntity(
            appid=appid,
            name=f"app_{appid}",
            store_url=f"https://store.steampowered.com/app/{appid}/",
            review_summary_url=f"https://store.steampowered.com/appreviews/{appid}?json=1",
        )

        details = collector.get_app_details(appid)
        if details:
            entity.name = details.get("name") or entity.name
            entity.is_free = bool(details.get("is_free"))
            from xhs_agent.domain.games.collectors.steam import _parse_release_date, _days_since_iso
            rd = details.get("release_date") or {}
            entity.release_date_iso = _parse_release_date(rd.get("date"))
            if entity.release_date_iso:
                entity.game_age_days = _days_since_iso(entity.release_date_iso)
            raw_genres = details.get("genres") or []
            entity.genres = [g.get("description", "") for g in raw_genres if g.get("description")]
            entity.short_description = (details.get("short_description") or "").strip() or None
            # Price info from details
            price_overview = details.get("price_overview") or {}
            if price_overview:
                entity.discount_pct = price_overview.get("discount_percent") or 0
                orig = price_overview.get("initial")
                final = price_overview.get("final")
                if orig:
                    entity.original_price = round(orig / 100, 2)
                if final:
                    entity.final_price = round(final / 100, 2)
                entity.is_on_special = (entity.discount_pct or 0) > 0

        collector._enrich_reviews(entity, language="schinese")

        # ITAD price history
        if entity.is_on_special and collector._itad:
            try:
                collector._itad.enrich_entity(entity)
            except Exception as exc:
                log.warning("manual_itad_failed", error=str(exc))

        # SteamCharts
        try:
            collector._steamcharts.enrich_entity(entity)
        except Exception as exc:
            log.warning("manual_steamcharts_failed", error=str(exc))

        return entity
    except Exception as exc:
        log.error("manual_collect_entity_failed", appid=appid, error=str(exc))
        return None


def _summarize_available_data(entity: GameEntity) -> dict:
    """Return a dict of bool flags indicating which data is available."""
    has_rate_trend = (
        entity.historical_positive_rate is not None
        and entity.recent_7d_positive_rate is not None
    )
    has_price_history = bool(entity.price_history)
    has_player_history = len(entity.player_count_history or []) >= 6
    has_review_pool = bool(entity.recent_review_pool)
    has_playtime_data = bool(entity.review_stats_pool)
    has_similar_games = len(entity.similar_games or []) >= 3

    return {
        "rate_trend": has_rate_trend,
        "theme_share": has_review_pool,
        "playtime_dist": has_playtime_data,
        "price_history": has_price_history or entity.is_on_special,
        "player_history": has_player_history,
        "similar_games": has_similar_games,
        "total_reviews": entity.total_reviews,
        "game_age_days": entity.game_age_days,
        "is_on_special": entity.is_on_special,
        "discount_pct": entity.discount_pct,
    }


def _make_judgment(content_plan: ContentPlan, entity: GameEntity) -> SignalJudgment:
    """Create a synthetic SignalJudgment for compatibility with generate_content()."""
    signal = SignalResult(
        entity_id=entity.appid,
        entity_name=entity.name,
        signal_type="manual_gen",
        score=1.0,
        severity="normal",
        raw_data={"user_direction": content_plan.key_narrative},
    )
    return SignalJudgment(
        signal=signal,
        worth_writing=True,
        template=content_plan.article_template,
        angle=content_plan.key_narrative,
        reasoning="Manual trigger",
    )


def _fetch_article_text(url: str) -> str:
    """Fetch plain text from an external article URL."""
    resp = httpx.get(url, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    # Strip HTML tags minimally
    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]


def _notify(message: str, chat_id: Optional[int], push: bool) -> None:
    if push:
        send_plain(message)

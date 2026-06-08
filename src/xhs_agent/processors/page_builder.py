"""Page builder — assembles the multi-page post structure from agent outputs.

No LLM cost. Converts generated content + buy_recommendation + review data into
the `pages` JSON array for S7 rendering.

P0 upgrade: dynamic page pool with eligibility gate.
Always-present: cover (1), conclusion (2), recommendation (last).
Conditional pool (in order):
  Slot A  rate_trend       → stats_summary_card if not eligible
  Slot B  theme_share      → risk_summary_card if not eligible
  Slot C  playtime_dist    → dropped if not eligible
  Slot D  review_quotes    → always added

Target 5-7 pages. Skipped/replaced pages are logged.

Each page dict includes:
  key_message  — observation-driven title for chart card header
  subtitle     — sample size / time window
  how_to_read  — "怎么读" text
  conclusion   — one-sentence takeaway
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from xhs_agent.observability.logger import get_logger
from xhs_agent.processors.chart_eligibility import (
    check_language_region_gap,
    check_playtime_dist,
    check_player_history,
    check_price_history,
    check_rate_trend,
    check_similar_games,
    check_theme_share,
)

if TYPE_CHECKING:
    from xhs_agent.agents.buy_or_wait import BuyRecommendation
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.domain.games.entity import GameEntity
    from xhs_agent.processors.playtime_buckets import PlaytimeBucketResult

log = get_logger(__name__)


def _is_prelaunch(entity: "GameEntity") -> bool:
    """True when the game has too few reviews for standard review-analysis content."""
    reviews_7d = getattr(entity, "recent_7d_review_count", None) or 0
    total = getattr(entity, "total_reviews", None) or 0
    return reviews_7d < 10 and total < 50


# Scalar entity fields the *subjective* cards (cover / combined_summary / —
# recommendation only needs buy_rec) read from `entity`. Frozen at generation
# time into `Post.objective_snapshot` so that a later rewrite can regenerate
# these cards with numbers that exactly match the originally-rendered objective
# chart images, even if live Steam data has since drifted.
_OBJECTIVE_SNAPSHOT_FIELDS = (
    "appid",
    "name",
    "historical_positive_rate",
    "recent_7d_positive_rate",
    "recent_7d_review_count",
    "total_reviews",
    "current_player_count",
)


def snapshot_objective_facts(entity: "GameEntity") -> dict:
    """Freeze the small set of scalar facts subjective cards depend on."""
    return {field: getattr(entity, field, None) for field in _OBJECTIVE_SNAPSHOT_FIELDS}


def _cover_page(
    entity: "GameEntity",
    buy_rec: "BuyRecommendation",
    title: str,
    content: str,
    cover_image_path: Optional[str] = None,
) -> dict:
    """Page 1: cover card. Shared by build_pages / build_pages_from_plan / rewrite.

    If `cover_image_path` is set (user uploaded a custom cover image), it's stashed
    in `data` so the renderer can use the user's artwork verbatim — no text overlay.
    """
    hook = _extract_hook(content) or title
    data = {"game_name": entity.name, "hook": hook}
    if cover_image_path:
        data["cover_image_path"] = cover_image_path
    return {
        "page": 1,
        "type": "cover",
        "title": title,
        "body": hook,
        "chart_type": "cover_card",
        "data": data,
        "key_message": title,
        "subtitle": "",
        "how_to_read": "",
        "conclusion": buy_rec.one_sentence,
        "image_path": None,
    }


def build_pages(
    *,
    title: str,
    content: str,
    entity: "GameEntity",
    buy_rec: "BuyRecommendation",
    theme_summary: Optional["ThemeSummary"] = None,
    playtime_buckets: Optional["PlaytimeBucketResult"] = None,
) -> list[dict]:
    """Return the dynamic page structure for a post (5-7 pages).

    Each page dict has: page, type, title, body, chart_type, data, image_path,
    key_message, subtitle, how_to_read, conclusion.
    """
    pages: list[dict] = []

    # ── Page 1: Cover (always) ─────────────────────────────────
    pages.append(_cover_page(entity, buy_rec, title, content))

    # ── Page 2: Combined summary (rating + stats + risks) ─────
    pages.append(_combined_summary_page(entity, buy_rec))

    # ── Slot A: game_info_card (prelaunch) OR rate_trend/stats_summary ───────
    if _is_prelaunch(entity):
        pages.append(_game_info_page(entity))
        log.info("page_added", type="game_info_card", reason="prelaunch_no_reviews")
    else:
        eligible, reason, _ = check_rate_trend(entity)
        if eligible:
            pages.append(_rate_trend_page(entity))
            log.info("page_added", type="trend", reason=reason)
        else:
            log.info("page_skipped", type="trend", reason=reason)

    # ── Slot B: theme_share (no text fallback — data is in combined_summary) ─
    eligible, reason, _ = check_theme_share(theme_summary, entity)
    if eligible:
        pages.append(_theme_share_page(theme_summary, entity))  # type: ignore[arg-type]
        log.info("page_added", type="theme_share", reason=reason)
    else:
        log.info("page_skipped", type="theme_share", reason=reason)

    # ── Slot C: playtime_dist or dropped ──────────────────────
    eligible, reason, _ = check_playtime_dist(playtime_buckets)
    if eligible:
        pages.append(_playtime_dist_page(playtime_buckets))  # type: ignore[arg-type]
        log.info("page_added", type="playtime_dist", reason=reason)
    else:
        log.info("page_skipped", type="playtime_dist", reason=reason, fallback=None)

    # ── Slot E: price history chart OR static price card ──────────────────
    eligible, reason, _ = check_price_history(entity)
    if eligible:
        pages.append(_price_history_page(entity, buy_rec))
        log.info("page_added", type="price_history", reason=reason)
    elif entity.is_on_special and entity.final_price is not None:
        pages.append(_price_page(entity, buy_rec))
        log.info("page_added", type="price_card",
                 is_at_low=entity.is_at_historic_low,
                 has_itad=entity.historic_low_price is not None)

    # ── Slot F: player count history (gated — needs meaningful trend) ─────
    eligible, reason, _ = check_player_history(entity)
    if eligible:
        pages.append(_player_history_page(entity))
        log.info("page_added", type="player_history", reason=reason)
    else:
        log.info("page_skipped", type="player_history", reason=reason)

    # ── Slot G: similar games comparison (gated — needs ≥3 peers) ─────────
    eligible, reason, _ = check_similar_games(entity)
    if eligible:
        pages.append(_similar_games_page(entity))
        log.info("page_added", type="similar_games", reason=reason)
    else:
        log.info("page_skipped", type="similar_games", reason=reason)

    # ── Slot H: language/region positive-rate gap (gated — only if there's a real story) ──
    eligible, reason, _ = check_language_region_gap(entity)
    if eligible:
        pages.append(_language_region_page(entity))
        log.info("page_added", type="language_region", reason=reason)
    else:
        log.info("page_skipped", type="language_region", reason=reason)

    # ── Slot D: review_quotes (gated — need ≥ 3 excerpts, skipped if trend card already embeds quotes) ─────
    trend_added = any(p.get("type") == "trend" for p in pages)
    excerpts = entity.sample_recent_review_excerpts or []
    if trend_added:
        log.info("page_skipped", type="review_quotes", reason="quotes_embedded_in_trend_card")
    elif len(excerpts) >= 3:
        pages.append(_review_quotes_page(entity, theme_summary))
    else:
        log.info("page_skipped", type="review_quotes",
                 reason=f"only_{len(excerpts)}_excerpts")

    # ── Last page: Recommendation (always) ────────────────────
    pages.append(_recommendation_page(buy_rec))

    # Re-number pages sequentially
    for i, p in enumerate(pages, start=1):
        p["page"] = i

    return pages


# ──────────────────────────────────────────────────────────────
# Page constructors
# ──────────────────────────────────────────────────────────────


def _combined_summary_page(entity: "GameEntity", buy_rec: "BuyRecommendation") -> dict:
    """Page 2: rating verdict + key stats + key risks in one structured card."""
    stats_lines: list[str] = []
    if entity.historical_positive_rate is not None:
        stats_lines.append(f"历史好评率：{round(entity.historical_positive_rate * 100)}%")
    if entity.recent_7d_positive_rate is not None:
        stats_lines.append(f"近7天好评率：{round(entity.recent_7d_positive_rate * 100)}%")
    if entity.recent_7d_review_count:
        stats_lines.append(f"近7天评论数：{entity.recent_7d_review_count}")
    if entity.total_reviews:
        stats_lines.append(f"总评论：{entity.total_reviews}")
    if entity.current_player_count:
        stats_lines.append(f"当前在线：{entity.current_player_count:,}")

    risks = buy_rec.key_risks[:3] if buy_rec.key_risks else []
    return {
        "page": 0,
        "type": "combined_summary_card",
        "title": f"{buy_rec.rating}级 — {buy_rec.rating_label()}",
        "body": buy_rec.one_sentence,
        "chart_type": "combined_summary_card",
        "data": {
            "rating": buy_rec.rating,
            "one_sentence": buy_rec.one_sentence,
            "stats": stats_lines,
            "risks": risks,
        },
        "key_message": f"{buy_rec.rating} — {buy_rec.one_sentence}",
        "subtitle": "",
        "how_to_read": "",
        "conclusion": buy_rec.one_sentence,
        "image_path": None,
    }


def _rate_trend_page(entity: "GameEntity") -> dict:
    hist = entity.historical_positive_rate or 0.0
    recent = entity.recent_7d_positive_rate or 0.0
    diff = recent - hist
    diff_pct = round(diff * 100, 1)
    direction = "回升" if diff > 0.02 else "下滑" if diff < -0.02 else "基本持平"
    sign = "+" if diff_pct > 0 else ""

    key_message = (
        f"近7天好评率{sign}{diff_pct}%，{direction}趋势"
        if abs(diff) >= 0.02
        else f"历史好评率{round(hist*100)}%，近7天{round(recent*100)}%，趋势稳定"
    )
    return {
        "page": 0,
        "type": "trend",
        "title": "好评率变化",
        "body": (
            f"历史好评率 {round(hist*100)}%，近 7 天 {round(recent*100)}%，"
            f"趋势{direction}（{sign}{diff_pct}%）"
        ),
        "chart_type": "positive_rate_timeline",
        "data": {
            "appid": entity.appid,
            "historical_positive_rate": hist,
            "recent_7d_positive_rate": recent,
        },
        "key_message": key_message,
        "subtitle": f"近7天评论数：{entity.recent_7d_review_count or '—'}　历史总评论：{entity.total_reviews or '—'}",
        "how_to_read": "绿柱=历史总好评率，红柱=近7天好评率。两者差距越大，说明口碑变化越明显。",
        "conclusion": f"近7天好评率{sign}{diff_pct}%，{'好于历史水平' if diff > 0 else '低于历史水平' if diff < 0 else '与历史持平'}。",
        "image_path": None,
    }


def _stats_summary_page(entity: "GameEntity", buy_rec: "BuyRecommendation") -> Optional[dict]:
    hist = entity.historical_positive_rate
    recent = entity.recent_7d_positive_rate
    lines = []
    if hist is not None:
        lines.append(f"历史好评率：{round(hist * 100)}%")
    if recent is not None:
        lines.append(f"近7天好评率：{round(recent * 100)}%")
    if entity.recent_7d_review_count:
        lines.append(f"近7天评论数：{entity.recent_7d_review_count}")
    if entity.total_reviews:
        lines.append(f"总评论数：{entity.total_reviews}")
    if entity.current_player_count:
        lines.append(f"当前在线：{entity.current_player_count:,}")
    if not lines:
        return None  # no real data — skip this page entirely
    body = "\n".join(lines)
    return {
        "page": 0,
        "type": "stats_summary_card",
        "title": "关键数据",
        "body": body,
        "chart_type": "text_card",
        "data": {},
        "key_message": "关键数据一览",
        "subtitle": "",
        "how_to_read": "",
        "conclusion": buy_rec.one_sentence,
        "image_path": None,
    }


def _game_info_page(entity: "GameEntity") -> dict:
    """Game information card for prelaunch / low-review games."""
    lines = []
    if entity.release_date_iso:
        age = entity.game_age_days
        if age is None or age < 0:
            suffix = "（未发售）"
        elif age == 0:
            suffix = "（今天发售）"
        else:
            suffix = f"（{age} 天前）"
        lines.append(f"发售日：{entity.release_date_iso}{suffix}")
    if entity.is_free:
        lines.append("价格：免费")
    elif entity.original_price is not None:
        price_str = f"¥{entity.original_price}"
        if entity.discount_pct:
            price_str += f" → ¥{entity.final_price}（-{entity.discount_pct}%）"
        lines.append(f"价格：{price_str}")
    if entity.genres:
        lines.append(f"类型：{' / '.join(entity.genres[:4])}")
    if entity.is_top_seller and entity.top_seller_rank:
        lines.append(f"Steam 热销榜：第 {entity.top_seller_rank} 名")
    if entity.current_player_count:
        lines.append(f"当前在线：{entity.current_player_count:,}")
    if entity.total_reviews:
        lines.append(f"总评论数：{entity.total_reviews}（数据较少）")
    if entity.short_description:
        lines.append(f"\n{entity.short_description[:120]}")
    body = "\n".join(lines) or "暂无详细数据"
    return {
        "page": 0,
        "type": "game_info_card",
        "title": "游戏基本信息",
        "body": body,
        "chart_type": "text_card",
        "data": {},
        "key_message": "发售信息一览",
        "subtitle": "来源：Steam 商店",
        "how_to_read": "",
        "conclusion": "",
        "image_path": None,
    }


def _theme_share_page(theme_summary: "ThemeSummary", entity: "GameEntity") -> dict:
    top3 = sorted(theme_summary.themes, key=lambda x: -x.negative_count)[:3]
    top_theme = top3[0] if top3 else None
    key_message = (
        f"差评最集中：{top_theme.theme}（占{top_theme.share_pct}%）"
        if top_theme
        else "差评主题分布"
    )
    theme_body = "差评最集中：" + " / ".join(
        f"{t.theme}({t.share_pct}%)" for t in top3 if t.negative_count > 0
    )
    theme_data = {
        t.theme: {"count": t.count, "negative_count": t.negative_count, "share_pct": t.share_pct}
        for t in theme_summary.themes if t.count > 0
    }
    total_neg = sum(t.negative_count for t in theme_summary.themes)
    return {
        "page": 0,
        "type": "theme_share",
        "title": "差评主要集中在哪里",
        "body": theme_body,
        "chart_type": "theme_share_bar",
        "data": {
            "themes": theme_data,
            "total_analyzed": getattr(theme_summary, "total_analyzed", 0),
        },
        "key_message": key_message,
        "subtitle": f"基于近期 {theme_summary.total_analyzed} 条评论分析",
        "how_to_read": "横条越长说明该主题出现频率越高；红色部分是差评，粉色是全部评论。",
        "conclusion": (
            f"差评最多的问题是{top_theme.theme}，占所有分析评论的{top_theme.share_pct}%。"
            if top_theme else "差评分布暂无数据。"
        ),
        "insights": [
            f"{t.theme}：{t.negative_count} 条差评（共 {t.count} 条提及）"
            for t in top3 if t.negative_count > 0
        ],
        "image_path": None,
    }


def _risk_summary_page(buy_rec: "BuyRecommendation") -> dict:
    risks = buy_rec.key_risks[:4] if buy_rec.key_risks else ["暂无数据"]
    body = "\n".join(f"• {r}" for r in risks)
    return {
        "page": 0,
        "type": "risk_summary_card",
        "title": "主要风险点",
        "body": body,
        "chart_type": "text_card",
        "data": {"risks": risks},
        "key_message": "购买前需注意的风险",
        "subtitle": "",
        "how_to_read": "",
        "conclusion": buy_rec.one_sentence,
        "image_path": None,
    }


def _playtime_dist_page(buckets: "PlaytimeBucketResult") -> dict:
    total = buckets.total
    short_pct = round(buckets.short_neg / total * 100)

    # Long-time positive rate: long_pos / (long_pos + long_neg)
    # — the share of long-time players who gave a thumbs-up,
    #   NOT long_pos / total (that would dilute across all reviews).
    long_time_total = buckets.long_pos + buckets.long_neg
    long_pos_of_long = round(buckets.long_pos / long_time_total * 100) if long_time_total else 0

    if short_pct >= 30:
        key_message = f"{short_pct}%的差评来自游玩不足2小时的玩家"
    elif long_pos_of_long >= 70:
        key_message = f"长时玩家（≥20h）中有{long_pos_of_long}%给出好评"
    else:
        key_message = "短时差评 vs 长时好评分布"

    long_neg = buckets.long_neg
    insights = [
        f"短时差评（<2h）{buckets.short_neg} 条，占总量 {short_pct}%",
        f"长时玩家（≥20h）好评率 {long_pos_of_long}%（{buckets.long_pos} 好评 / {buckets.long_neg} 差评）",
    ]
    return {
        "page": 0,
        "type": "playtime_dist",
        "title": "什么类型的玩家在评论",
        "body": buckets.format_for_prompt(),
        "chart_type": "playtime_distribution",
        "data": buckets.as_dict(),
        "key_message": key_message,
        "subtitle": f"样本：{total} 条有时长数据的评论",
        "how_to_read": "红色=差评，绿色=好评；按游玩时长分三组。长时好评率=长时好评÷（长时好评+长时差评）。",
        "conclusion": (
            f"短时差评占全部的{short_pct}%，长时玩家中{long_pos_of_long}%给好评——"
            + ("短时差评较多，可能存在上手门槛或首发问题。" if short_pct >= 30 else "核心玩家认可度较高。")
        ),
        "insights": insights,
        "image_path": None,
    }


def _review_quotes_page(
    entity: "GameEntity",
    theme_summary: Optional["ThemeSummary"],
) -> dict:
    quotes = entity.sample_recent_review_excerpts[:3]
    quotes_body = "\n\n".join(f"「{q[:100]}」" for q in quotes) if quotes else "(无代表性评论)"
    return {
        "page": 0,
        "type": "review_quotes",
        "title": "玩家原话",
        "body": quotes_body,
        "chart_type": "review_card",
        "data": {"quotes": [q[:100] for q in quotes]},
        "key_message": "玩家是怎么说的",
        "subtitle": "Steam 近期评论精选",
        "how_to_read": "",
        "conclusion": "",
        "image_path": None,
    }


def _price_page(entity: "GameEntity", buy_rec: "BuyRecommendation") -> dict:
    """Price comparison card for discounted games (Slot E)."""
    if entity.is_at_historic_low:
        key_message = f"当前折扣接近历史最低（史低 ¥{entity.historic_low_price}）"
        conclusion = f"史低附近，折后 ¥{entity.final_price}，是近年来最低价之一。"
    elif entity.historic_low_price is not None and entity.original_price and entity.original_price > 0:
        # Compare discount depths: current cut% vs historic low cut%
        orig = entity.original_price
        hist_cut = round((orig - entity.historic_low_price) / orig * 100)
        curr_cut = entity.discount_pct or 0
        gap = hist_cut - curr_cut
        key_message = (
            f"当前直降 {curr_cut}%（折后 ¥{entity.final_price}），"
            f"史低曾直降 {hist_cut}%（¥{entity.historic_low_price}），差 {gap} 个百分点"
        )
        conclusion = (
            f"折扣力度比史低低 {gap} 个百分点，但已有 {curr_cut}% 优惠。"
            f"评级 {buy_rec.rating} — {buy_rec.rating_label()}。"
        )
    else:
        key_message = f"直降 {entity.discount_pct}%，折后 ¥{entity.final_price}"
        conclusion = f"有折扣，无历史价格数据参考。当前评级 {buy_rec.rating}。"

    return {
        "page": 0,
        "type": "price_card",
        "title": "折扣价格分析",
        "body": conclusion,
        "chart_type": "price_card",
        "data": {
            "current_price": entity.final_price,
            "original_price": entity.original_price,
            "historic_low": entity.historic_low_price,
            "pct_above_low": entity.pct_above_historic_low,
            "is_at_historic_low": entity.is_at_historic_low,
            "discount_pct": entity.discount_pct,
        },
        "key_message": key_message,
        "subtitle": "来源：IsThereAnyDeal + Steam" if entity.historic_low_price else "来源：Steam",
        "how_to_read": "红色条=折后价，绿色条=历史最低。两者越接近，折扣越有吸引力。",
        "conclusion": conclusion,
        "image_path": None,
    }


def _player_history_page(entity: "GameEntity") -> dict:
    """Player count history line chart page (Slot F)."""
    history = entity.player_count_history or []
    peaks = [r["peak"] for r in history]
    all_time_peak = getattr(entity, "player_count_all_time_peak", None)
    peak_month = getattr(entity, "player_count_peak_month", None)
    months = len(history)

    # Use recent 3m vs prev 3m for the narrative (same logic as eligibility check)
    recent_avg = int(sum(peaks[-3:]) / 3) if len(peaks) >= 3 else peaks[-1] if peaks else 0
    prev_avg = int(sum(peaks[-6:-3]) / 3) if len(peaks) >= 6 else 0
    recent_change = (recent_avg - prev_avg) / prev_avg if prev_avg > 0 else 0

    if recent_change <= -0.20:
        pct_abs = abs(round(recent_change * 100))
        key_message = f"近3个月在线持续下滑 {pct_abs}%"
        conclusion = f"近3个月均值 {recent_avg:,}，较前3个月下降 {pct_abs}%，玩家活跃度在减退。"
    elif recent_change >= 0.15:
        pct_abs = round(recent_change * 100)
        key_message = f"近3个月在线增长 {pct_abs}%，热度回升"
        conclusion = f"近3个月均值 {recent_avg:,}，较前3个月增长 {pct_abs}%，热度正在上升。"
    else:
        key_message = f"在线人数历史趋势（峰值 {all_time_peak:,}，{peak_month}）" if all_time_peak else "在线人数历史趋势"
        conclusion = f"近期在线基本平稳，均值约 {recent_avg:,} 人。"
    return {
        "page": 0,
        "type": "player_history",
        "title": "在线人数历史",
        "body": conclusion,
        "chart_type": "player_history",
        "data": {},
        "key_message": key_message,
        "subtitle": f"数据来源：SteamCharts（近 {months} 个月）",
        "how_to_read": "黄点=历史最高峰，绿点=当前",
        "conclusion": conclusion,
        "insights": [x for x in [
            f"历史峰值：{all_time_peak:,} 人（{peak_month}）" if all_time_peak and peak_month else None,
            f"近3个月均值：{recent_avg:,} 人" if recent_avg else None,
        ] if x],
        "image_path": None,
    }


def _price_history_page(entity: "GameEntity", buy_rec: "BuyRecommendation") -> dict:
    """Price history step-line chart for discounted games with ITAD history (replaces Slot E static card)."""
    history = entity.price_history or []
    discount_events = [e for e in history if e.get("cut", 0) > 0]
    min_price = min((e["price"] for e in history), default=entity.final_price)
    regular = history[0]["regular"] if history else entity.original_price

    if entity.is_at_historic_low:
        key_message = f"当前折扣接近历史最低（史低 ¥{min_price:.2f}）"
    elif discount_events and regular and regular > 0 and entity.final_price is not None:
        hist_cut = round((regular - min_price) / regular * 100)
        curr_cut = entity.discount_pct or round((regular - entity.final_price) / regular * 100)
        gap = hist_cut - curr_cut
        key_message = f"当前直降 {curr_cut}%，史低曾直降 {hist_cut}%，差距 {gap} 个百分点"
    else:
        key_message = f"折后 ¥{entity.final_price}，原价 ¥{regular}"

    conclusion = f"折扣历史一览，当前评级 {buy_rec.rating} — {buy_rec.rating_label()}。"
    disc_count = len(discount_events)
    insights = [x for x in [
        f"共记录 {disc_count} 次折扣活动" if disc_count else None,
        f"史低价格：¥{min_price:.2f}（原价 ¥{regular:.2f}）" if regular and regular > 0 else None,
        f"当前直降 {entity.discount_pct}%，折后 ¥{entity.final_price}" if entity.discount_pct else None,
    ] if x]
    return {
        "page": 0,
        "type": "price_history",
        "title": "价格历史",
        "body": conclusion,
        "chart_type": "price_history",
        "data": {},
        "key_message": key_message,
        "subtitle": "数据来源：IsThereAnyDeal",
        "how_to_read": "红色区域=折扣期，灰线=原价，绿色标注=史低",
        "conclusion": conclusion,
        "insights": insights,
        "image_path": None,
    }


def _similar_games_page(entity: "GameEntity") -> dict:
    """Similar games positive-rate comparison chart (Slot G)."""
    peers = entity.similar_games or []
    target_rate = round((entity.historical_positive_rate or 0) * 100, 1)
    peer_avg = round(
        sum(p["positive_rate"] for p in peers) / len(peers) * 100, 1
    ) if peers else 0.0

    if target_rate >= peer_avg + 5:
        key_message = f"好评率 {target_rate}%，高于同类均值 {peer_avg}%"
        conclusion = f"在同类游戏中口碑较好，同类均值 {peer_avg}%。"
    elif target_rate <= peer_avg - 5:
        key_message = f"好评率 {target_rate}%，低于同类均值 {peer_avg}%"
        conclusion = f"在同类中口碑偏低，同类均值 {peer_avg}%，需关注差评原因。"
    else:
        key_message = f"好评率 {target_rate}%，与同类持平（均值 {peer_avg}%）"
        conclusion = f"口碑与同类游戏相当，同类均值 {peer_avg}%。"

    return {
        "page": 0,
        "type": "similar_games",
        "title": "同类游戏对比",
        "body": conclusion,
        "chart_type": "similar_games",
        "data": {},
        "key_message": key_message,
        "subtitle": f"数据来源：SteamSpy（{len(peers)} 款同类游戏）",
        "how_to_read": "红色条=本游戏，灰色条=同类游戏，按好评率排序",
        "conclusion": conclusion,
        "insights": [x for x in [
            f"本游戏好评率：{target_rate}%",
            f"同类 {len(peers)} 款均值：{peer_avg}%",
        ] if x],
        "image_path": None,
    }


_LANG_LABELS_CN = {
    "schinese": "国区（简中）",
    "tchinese": "繁中",
    "english": "英语区",
    "russian": "俄语区",
    "japanese": "日语区",
    "koreana": "韩语区",
    "german": "德语区",
    "french": "法语区",
    "spanish": "西语区",
    "polish": "波兰语区",
    "portuguese": "葡语区",
    "brazilian": "巴西区",
    "turkish": "土耳其区",
}
_CN_LANGS = {"schinese", "tchinese"}


def _language_region_page(entity: "GameEntity") -> dict:
    """Per-language/region positive-rate comparison (gated — needs a real gap)."""
    rates: dict = entity.review_positive_rate_by_language or {}
    dist: dict = entity.review_language_dist or {}
    rows = sorted(rates.items(), key=lambda kv: -kv[1])

    cn_rate = next((r for lang, r in rows if lang in _CN_LANGS), None)
    other_rows = [(lang, r) for lang, r in rows if lang not in _CN_LANGS]
    best = rows[0]
    worst = rows[-1]

    def _label(lang: str) -> str:
        return _LANG_LABELS_CN.get(lang, lang)

    if cn_rate is not None and other_rows:
        gap_lang, gap_rate = max(other_rows, key=lambda kv: abs(kv[1] - cn_rate))
        if cn_rate > gap_rate:
            key_message = f"国区好评率 {cn_rate*100:.0f}%，{_label(gap_lang)}仅 {gap_rate*100:.0f}%"
            conclusion = f"国区玩家明显更买账，{_label(gap_lang)}口碑落后 {abs(cn_rate-gap_rate)*100:.0f} 个百分点，可能涉及本地化、定价或文化差异。"
        else:
            key_message = f"{_label(gap_lang)}好评率 {gap_rate*100:.0f}%，国区仅 {cn_rate*100:.0f}%"
            conclusion = f"国区口碑落后 {_label(gap_lang)} {abs(cn_rate-gap_rate)*100:.0f} 个百分点，值得关注国区特有的吐槽点（本地化/联机/定价）。"
    else:
        key_message = f"{_label(best[0])} {best[1]*100:.0f}% vs {_label(worst[0])} {worst[1]*100:.0f}%"
        conclusion = f"不同地区好评率差异明显，最高与最低相差 {abs(best[1]-worst[1])*100:.0f} 个百分点。"

    insights = [
        f"{_label(lang)}：历史好评率 {rate*100:.0f}%（共 {dist.get(lang, 0):,} 条评论）"
        for lang, rate in rows[:10]
    ]

    return {
        "page": 0,
        "type": "language_region",
        "title": "各地区好评率对比",
        "body": conclusion,
        "chart_type": "language_region",
        "data": {},
        "key_message": key_message,
        "subtitle": "各地区历史全部评论的好评率 · 好评/差评判定取自 Steam 官方 voted_up 标记",
        "how_to_read": "红色条=国区，灰色条=其他地区；好评率=该地区历史全部评论中 Steam 标记为「推荐」的占比，按好评率排序",
        "conclusion": conclusion,
        "insights": insights,
        "image_path": None,
    }


def _recommendation_page(buy_rec: "BuyRecommendation") -> dict:
    return {
        "page": 0,
        "type": "recommendation",
        "title": f"购买建议 {buy_rec.rating} 级 — {buy_rec.rating_label()}",
        "body": buy_rec.format_for_page(),
        "chart_type": "recommendation_card",
        "data": {
            "rating": buy_rec.rating,
            "suitable_for": buy_rec.suitable_for,
            "not_suitable_for": buy_rec.not_suitable_for,
            "key_risks": buy_rec.key_risks,
            "wait_for": buy_rec.wait_for,
        },
        "key_message": f"{buy_rec.rating} 级 — {buy_rec.rating_label()}",
        "subtitle": "",
        "how_to_read": "",
        "conclusion": buy_rec.one_sentence,
        "image_path": None,
    }


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _extract_hook(content: str) -> Optional[str]:
    """Pull the first non-empty sentence from content."""
    for line in content.splitlines():
        line = line.strip()
        if line and len(line) >= 10:
            for sep in ("。", "！", "？", "…"):
                idx = line.find(sep)
                if 10 <= idx <= 60:
                    return line[: idx + 1]
            if len(line) <= 60:
                return line
    return None


def build_pages_from_plan(
    *,
    title: str,
    content: str,
    entity: "GameEntity",
    buy_rec: "BuyRecommendation",
    content_plan,
    theme_summary: Optional["ThemeSummary"] = None,
    playtime_buckets: Optional["PlaytimeBucketResult"] = None,
) -> list[dict]:
    """Build pages following ContentPlan.charts_needed (bypasses eligibility gates).

    Always includes: cover (1), combined_summary_card (2), recommendation (last).
    Chart pages are added in the order specified by content_plan.charts_needed.
    If data for a chart is missing, it is skipped with a warning.
    """
    from xhs_agent.agents.content_director import AVAILABLE_CHART_TYPES

    pages: list[dict] = []

    # ── Page 1: Cover ─────────────────────────────────────────
    pages.append(_cover_page(entity, buy_rec, title, content))

    # ── Page 2: Combined summary ───────────────────────────────
    pages.append(_combined_summary_page(entity, buy_rec))

    # ── Chart pages (plan-driven, no eligibility gate) ─────────
    for chart_type in (content_plan.charts_needed or []):
        if chart_type == "rate_trend":
            if entity.historical_positive_rate is not None and entity.recent_7d_positive_rate is not None:
                pages.append(_rate_trend_page(entity))
            else:
                log.warning("plan_chart_skip", chart="rate_trend", reason="missing_rate_data")

        elif chart_type == "theme_share":
            if theme_summary and theme_summary.themes:
                pages.append(_theme_share_page(theme_summary, entity))
            else:
                log.warning("plan_chart_skip", chart="theme_share", reason="no_theme_summary")

        elif chart_type == "playtime_dist":
            if playtime_buckets and playtime_buckets.total > 0:
                pages.append(_playtime_dist_page(playtime_buckets))
            else:
                log.warning("plan_chart_skip", chart="playtime_dist", reason="no_playtime_data")

        elif chart_type == "price_history":
            if entity.price_history:
                pages.append(_price_history_page(entity, buy_rec))
            elif entity.is_on_special and entity.final_price is not None:
                pages.append(_price_page(entity, buy_rec))
            else:
                log.warning("plan_chart_skip", chart="price_history", reason="no_price_data")

        elif chart_type == "player_history":
            if entity.player_count_history and len(entity.player_count_history) >= 3:
                pages.append(_player_history_page(entity))
            else:
                log.warning("plan_chart_skip", chart="player_history", reason="insufficient_history")

        elif chart_type == "similar_games":
            if entity.similar_games and len(entity.similar_games) >= 3:
                pages.append(_similar_games_page(entity))
            else:
                log.warning("plan_chart_skip", chart="similar_games", reason="no_peers")

    # ── Review quotes (always try to add) ─────────────────────
    excerpts = entity.sample_recent_review_excerpts or []
    if len(excerpts) >= 3:
        pages.append(_review_quotes_page(entity, theme_summary))

    # ── Last page: Recommendation ──────────────────────────────
    pages.append(_recommendation_page(buy_rec))

    # Re-number pages sequentially
    for i, p in enumerate(pages, start=1):
        p["page"] = i

    return pages

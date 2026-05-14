"""Orchestrate rendering all pages → list of image paths."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from xhs_agent.observability.logger import get_logger

if TYPE_CHECKING:
    from xhs_agent.agents.buy_or_wait import BuyRecommendation
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.domain.games.entity import GameEntity
    from xhs_agent.processors.playtime_buckets import PlaytimeBucketResult

log = get_logger(__name__)


def render_all_pages(
    *,
    entity: "GameEntity",
    buy_rec: "BuyRecommendation",
    pages: list[dict],
    theme_summary: Optional["ThemeSummary"] = None,
    playtime_buckets: Optional["PlaytimeBucketResult"] = None,
    title: str = "",
    out_dir: Optional[Path] = None,
) -> list[dict]:
    """Render images for each page and fill `page["image_path"]`.

    Returns the modified pages list with image_path set where rendering succeeded.
    Failures are logged as warnings but never raise — the pipeline continues without images.
    """
    if out_dir is None:
        out_dir = Path(tempfile.mkdtemp(prefix="xhs_viz_"))

    appid = getattr(entity, "appid", "unknown")

    renderers = {
        "cover":                _render_cover,
        "combined_summary_card": _render_combined_summary,
        "trend":                _render_trend,
        "theme_share":          _render_theme_share,
        "playtime_dist":        _render_playtime,
        "review_quotes":        _render_review_quotes,
        "recommendation":       _render_recommendation,
        "price_card":           _render_price_card,
        "price_history":        _render_price_history,
        "player_history":       _render_player_history,
        "similar_games":        _render_similar_games,
        "game_info_card":       _render_text_card,
        # kept for safety / backward-compat with existing post records
        "conclusion":           _render_text_card,
        "stats_summary_card":   _render_text_card,
        "risk_summary_card":    _render_risk_card,
    }

    for page in pages:
        page_type = page.get("type", "")
        renderer = renderers.get(page_type)
        if renderer is None:
            log.warning("page_renderer_missing", type=page_type)
            continue

        out_path = out_dir / f"{appid}_p{page['page']}_{page_type}.png"
        try:
            renderer(
                page=page,
                entity=entity,
                buy_rec=buy_rec,
                theme_summary=theme_summary,
                playtime_buckets=playtime_buckets,
                title=title,
                out_path=out_path,
            )
            page["image_path"] = str(out_path)
            log.info("page_rendered", page=page["page"], type=page_type, path=str(out_path))
        except Exception as exc:
            log.warning("page_render_failed", page=page["page"], type=page_type, error=str(exc))

    return pages


# ── per-type render dispatch ──────────────────────────────────────────────────


def _render_cover(*, page, entity, buy_rec, title, out_path, **_kw) -> None:
    from xhs_agent.visualization.card_cover import render_cover
    render_cover(entity=entity, buy_rec=buy_rec, out_path=out_path, title=title)


def _render_text_card(*, page, entity, buy_rec, out_path, **_kw) -> None:
    """Conclusion / stats_summary_card / game_info_card / generic text page."""
    from PIL import Image, ImageDraw, ImageFont
    from xhs_agent.visualization.base import (
        ACCENT_COLOR, BORDER_COLOR, CARD_BG, FONT_BOLD, FONT_REGULAR,
        IMG_H, IMG_W, TEXT_PRIMARY, TEXT_SECONDARY, draw_wrapped,
    )

    img = Image.new("RGB", (IMG_W, IMG_H), color=CARD_BG)
    draw = ImageDraw.Draw(img)
    try:
        font_h    = ImageFont.truetype(FONT_BOLD    or "Arial", 50) if FONT_BOLD    else ImageFont.load_default()
        font_body = ImageFont.truetype(FONT_REGULAR or "Arial", 30) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_h = font_body = ImageFont.load_default()

    draw.rectangle([0, 0, IMG_W, 14], fill=ACCENT_COLOR)
    draw.text((60, 54), page.get("title", ""), font=font_h, fill=TEXT_PRIMARY)
    draw.rectangle([60, 124, IMG_W - 60, 127], fill=BORDER_COLOR)
    draw_wrapped(draw, page.get("body", ""), font_body, TEXT_PRIMARY,
                 x=60, y=152, max_width=960, line_spacing=14)
    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)


def _render_combined_summary(*, page, buy_rec, entity, out_path, **_kw) -> None:
    """Three-band card: rating verdict (accent) | key stats | key risks."""
    from PIL import Image, ImageDraw, ImageFont
    from xhs_agent.visualization.base import (
        ACCENT_COLOR, BORDER_COLOR, CARD_BG, COLOR_PRICE, FONT_BOLD, FONT_REGULAR,
        IMG_H, IMG_W, RATING_COLORS, TEXT_PRIMARY, TEXT_SECONDARY, draw_wrapped,
    )

    img = Image.new("RGB", (IMG_W, IMG_H), color=CARD_BG)
    draw = ImageDraw.Draw(img)

    try:
        font_rating  = ImageFont.truetype(FONT_BOLD    or "Arial", 110) if FONT_BOLD    else ImageFont.load_default()
        font_label   = ImageFont.truetype(FONT_BOLD    or "Arial", 40)  if FONT_BOLD    else ImageFont.load_default()
        font_verdict = ImageFont.truetype(FONT_REGULAR or "Arial", 32)  if FONT_REGULAR else ImageFont.load_default()
        font_sec_hdr = ImageFont.truetype(FONT_BOLD    or "Arial", 30)  if FONT_BOLD    else ImageFont.load_default()
        font_stat    = ImageFont.truetype(FONT_REGULAR or "Arial", 28)  if FONT_REGULAR else ImageFont.load_default()
        font_risk    = ImageFont.truetype(FONT_REGULAR or "Arial", 26)  if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_rating = font_label = font_verdict = font_sec_hdr = font_stat = font_risk = ImageFont.load_default()

    data = page.get("data", {})
    rating = data.get("rating") or (buy_rec.rating if buy_rec else "C")
    one_sentence = data.get("one_sentence") or page.get("body", "")
    stats = data.get("stats") or []
    risks = data.get("risks") or []
    label = buy_rec.rating_label() if buy_rec else ""
    rating_color = RATING_COLORS.get(rating, ACCENT_COLOR)

    # ── Band 1: Rating + Verdict (0–370) ────────────────────────
    BAND1_H = 370
    draw.rectangle([0, 0, IMG_W, BAND1_H], fill=rating_color)

    draw.text((60, 40), rating, font=font_rating, fill="white")
    try:
        letter_w = font_rating.getbbox(rating)[2] + 80
    except Exception:
        letter_w = 200
    draw.text((letter_w, 60), label, font=font_label, fill="white")
    draw_wrapped(draw, one_sentence, font_verdict, "white",
                 x=letter_w, y=126, max_width=IMG_W - letter_w - 60, line_spacing=10)

    try:
        tag = "真实玩家评论研究所"
        tag_w = font_risk.getbbox(tag)[2] - font_risk.getbbox(tag)[0]
    except Exception:
        tag_w = 200
    draw.text((IMG_W - tag_w - 30, BAND1_H - 38), tag, font=font_risk, fill="white")

    # ── Band 2: Key Stats (370–740) ─────────────────────────────
    BAND2_Y = BAND1_H
    BAND2_H = 370
    draw.rectangle([0, BAND2_Y, IMG_W, BAND2_Y + BAND2_H], fill="#1A2030")
    draw.text((60, BAND2_Y + 22), "关键数据", font=font_sec_hdr, fill=ACCENT_COLOR)

    sy = BAND2_Y + 76
    for stat_line in stats[:6]:
        draw.rectangle([60, sy + 5, 66, sy + 29], fill=ACCENT_COLOR)
        draw.text((82, sy), stat_line, font=font_stat, fill=TEXT_PRIMARY)
        sy += 50

    # ── Band 3: Key Risks (740–1380) ────────────────────────────
    BAND3_Y = BAND2_Y + BAND2_H
    draw.rectangle([0, BAND3_Y, IMG_W, IMG_H], fill="#1E1A14")
    draw.text((60, BAND3_Y + 22), "主要风险", font=font_sec_hdr, fill=COLOR_PRICE)

    ry = BAND3_Y + 78
    for risk in risks[:5]:
        draw.ellipse([60, ry + 8, 76, ry + 24], fill=COLOR_PRICE)
        ry = draw_wrapped(draw, risk, font_risk, TEXT_PRIMARY,
                          x=90, y=ry, max_width=920, line_spacing=8)
        ry += 16

    # ── Bottom accent strip ─────────────────────────────────────
    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)


def _render_risk_card(*, page, buy_rec, out_path, **_kw) -> None:
    """Renders key risks as a styled text card."""
    from PIL import Image, ImageDraw, ImageFont
    from xhs_agent.visualization.base import (
        ACCENT_COLOR, BORDER_COLOR, CARD_BG, COLOR_PRICE, FONT_BOLD, FONT_REGULAR,
        IMG_H, IMG_W, TEXT_PRIMARY, TEXT_SECONDARY, draw_wrapped,
    )

    img = Image.new("RGB", (IMG_W, IMG_H), color=CARD_BG)
    draw = ImageDraw.Draw(img)
    try:
        font_h    = ImageFont.truetype(FONT_BOLD    or "Arial", 50) if FONT_BOLD    else ImageFont.load_default()
        font_body = ImageFont.truetype(FONT_REGULAR or "Arial", 30) if FONT_REGULAR else ImageFont.load_default()
        font_sub  = ImageFont.truetype(FONT_REGULAR or "Arial", 22) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_h = font_body = font_sub = ImageFont.load_default()

    draw.rectangle([0, 0, IMG_W, 14], fill=ACCENT_COLOR)
    draw.text((60, 24), "真实玩家评论研究所", font=font_sub, fill=ACCENT_COLOR)
    draw.text((60, 54), page.get("title", "主要风险点"), font=font_h, fill=TEXT_PRIMARY)
    draw.rectangle([60, 124, IMG_W - 60, 127], fill=BORDER_COLOR)

    risks = page.get("data", {}).get("risks") or []
    if not risks and buy_rec:
        risks = buy_rec.key_risks[:5] if buy_rec.key_risks else []

    y = 162
    for risk in risks[:6]:
        draw.ellipse([60, y + 10, 76, y + 26], fill=COLOR_PRICE)
        y = draw_wrapped(draw, risk, font_body, TEXT_PRIMARY,
                         x=90, y=y, max_width=930, line_spacing=8)
        y += 18

    if page.get("conclusion"):
        draw.rectangle([60, IMG_H - 130, IMG_W - 60, IMG_H - 128], fill=BORDER_COLOR)
        draw_wrapped(draw, page["conclusion"], font_sub, TEXT_SECONDARY,
                     x=60, y=IMG_H - 120, max_width=960, line_spacing=6)

    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)


def _render_price_card(*, page, entity, out_path, **_kw) -> None:
    from xhs_agent.visualization.card_price import render_price_card
    render_price_card(entity=entity, page=page, out_path=out_path)


def _render_price_history(*, page, entity, out_path, **_kw) -> None:
    from xhs_agent.visualization.chart_price_history import render_price_history_chart
    render_price_history_chart(entity=entity, page=page, out_path=out_path)


def _render_player_history(*, page, entity, out_path, **_kw) -> None:
    from xhs_agent.visualization.chart_player_history import render_player_history_chart
    render_player_history_chart(entity=entity, page=page, out_path=out_path)


def _render_similar_games(*, page, entity, out_path, **_kw) -> None:
    from xhs_agent.visualization.chart_similar_games import render_similar_games_chart
    render_similar_games_chart(entity=entity, page=page, out_path=out_path)


def _render_trend(*, page, entity, theme_summary, out_path, **_kw) -> None:
    from xhs_agent.visualization.card_sentiment import render_sentiment_card
    render_sentiment_card(entity=entity, page=page, out_path=out_path, theme_summary=theme_summary)


def _render_theme_share(*, page, theme_summary, out_path, **_kw) -> None:
    if theme_summary and theme_summary.success:
        from xhs_agent.visualization.chart_theme_share import render_theme_share_chart
        render_theme_share_chart(theme_summary=theme_summary, page=page, out_path=out_path)
    else:
        _placeholder(out_path, "暂无主题数据")


def _render_playtime(*, page, playtime_buckets, out_path, **_kw) -> None:
    if playtime_buckets and playtime_buckets.total > 0:
        from xhs_agent.visualization.chart_playtime_distribution import render_playtime_chart
        render_playtime_chart(buckets=playtime_buckets, page=page, out_path=out_path)
    else:
        _placeholder(out_path, "暂无游玩时长数据")


def _render_review_quotes(*, entity, theme_summary, out_path, **_kw) -> None:
    from xhs_agent.visualization.card_review import render_review_card
    render_review_card(entity=entity, theme_summary=theme_summary, out_path=out_path)


def _render_recommendation(*, buy_rec, out_path, **_kw) -> None:
    from xhs_agent.visualization.card_recommendation import render_recommendation_card
    render_recommendation_card(buy_rec=buy_rec, out_path=out_path)


def _placeholder(out_path: Path, msg: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    from xhs_agent.visualization.base import CARD_BG, FONT_REGULAR, IMG_H, IMG_W, TEXT_SECONDARY

    img = Image.new("RGB", (IMG_W, IMG_H), CARD_BG)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT_REGULAR or "Arial", 36) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font = ImageFont.load_default()
    draw.text((IMG_W // 2, IMG_H // 2), msg, font=font, fill=TEXT_SECONDARY, anchor="mm")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG")

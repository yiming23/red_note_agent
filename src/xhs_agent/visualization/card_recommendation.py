"""Page 7 — Buy-or-Wait recommendation card (Pillow)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from xhs_agent.visualization.base import (
    ACCENT_COLOR,
    BG_COLOR,
    BORDER_COLOR,
    CARD_BG,
    FONT_BOLD,
    FONT_REGULAR,
    IMG_H,
    IMG_W,
    RATING_COLORS,
    RATING_LABELS,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
)

if TYPE_CHECKING:
    from xhs_agent.agents.buy_or_wait import BuyRecommendation


def render_recommendation_card(buy_rec: "BuyRecommendation", out_path: Path) -> Path:
    """Render A-E rating + details card."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (IMG_W, IMG_H), color=CARD_BG)
    draw = ImageDraw.Draw(img)

    try:
        font_huge = ImageFont.truetype(FONT_BOLD or "Arial", 120) if FONT_BOLD else ImageFont.load_default()
        font_h = ImageFont.truetype(FONT_BOLD or "Arial", 46) if FONT_BOLD else ImageFont.load_default()
        font_sub = ImageFont.truetype(FONT_REGULAR or "Arial", 30) if FONT_REGULAR else ImageFont.load_default()
        font_body = ImageFont.truetype(FONT_REGULAR or "Arial", 26) if FONT_REGULAR else ImageFont.load_default()
        font_tag = ImageFont.truetype(FONT_REGULAR or "Arial", 20) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_huge = font_h = font_sub = font_body = font_tag = ImageFont.load_default()

    rating = buy_rec.rating if buy_rec and buy_rec.success else "C"
    rc = RATING_COLORS.get(rating, ACCENT_COLOR)
    rl = RATING_LABELS.get(rating, rating)

    # top color band — rating color fills first 340px
    draw.rectangle([0, 0, IMG_W, 340], fill=rc)

    # grade letter
    draw.text((IMG_W // 2, 80), rating, font=font_huge, fill="white", anchor="mt")
    draw.text((IMG_W // 2, 224), rl, font=font_h, fill="white", anchor="mt")

    # one sentence
    y = 370
    draw.text((IMG_W // 2, y), buy_rec.one_sentence or "", font=font_sub,
              fill=TEXT_PRIMARY, anchor="mt")
    y += 68

    draw.rectangle([60, y, IMG_W - 60, y + 2], fill=BORDER_COLOR)
    y += 24

    def _section(title: str, items: list[str], color: str) -> int:
        nonlocal y
        if not items:
            return y
        draw.text((60, y), title, font=font_sub, fill=color)
        y += 48
        for item in items[:4]:
            draw.text((80, y), f"• {item}", font=font_body, fill=TEXT_PRIMARY)
            y += 40
        y += 10
        return y

    _section("✅ 适合谁", buy_rec.suitable_for, "#27AE60")
    _section("❌ 不适合谁", buy_rec.not_suitable_for, ACCENT_COLOR)
    _section("⚠️ 关键风险", buy_rec.key_risks, "#FF9800")

    if buy_rec.wait_for:
        draw.text((60, y), f"⏳ 如果等：{buy_rec.wait_for}", font=font_body, fill=TEXT_SECONDARY)
        y += 44

    # footer strip
    draw.rectangle([0, IMG_H - 60, IMG_W, IMG_H], fill=rc)
    draw.text((IMG_W // 2, IMG_H - 40), "真实玩家评论研究所", font=font_tag, fill="white", anchor="mt")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)
    return out_path

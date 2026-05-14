"""Price comparison card — current price vs historic low (S8).

Shows a simple visual: current discounted price vs Steam all-time low,
discount bar, and a verdict label ("接近史低" / "距史低 X%").
Falls back gracefully to a text-only card when ITAD data is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity


def render_price_card(*, entity: "GameEntity", page: dict, out_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    from xhs_agent.visualization.base import (
        ACCENT_COLOR, BORDER_COLOR, CARD_BG, COLOR_POSITIVE, COLOR_PRICE,
        FONT_BOLD, FONT_REGULAR, IMG_H, IMG_W,
        TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, draw_wrapped,
    )

    img = Image.new("RGB", (IMG_W, IMG_H), CARD_BG)
    draw = ImageDraw.Draw(img)

    try:
        font_h    = ImageFont.truetype(FONT_BOLD    or "Arial", 50) if FONT_BOLD    else ImageFont.load_default()
        font_sub  = ImageFont.truetype(FONT_BOLD    or "Arial", 34) if FONT_BOLD    else ImageFont.load_default()
        font_body = ImageFont.truetype(FONT_REGULAR or "Arial", 28) if FONT_REGULAR else ImageFont.load_default()
        font_sm   = ImageFont.truetype(FONT_REGULAR or "Arial", 22) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_h = font_sub = font_body = font_sm = ImageFont.load_default()

    # ── Top accent strip ─────────────────────────────────────
    draw.rectangle([0, 0, IMG_W, 14], fill=ACCENT_COLOR)
    draw.text((60, 24), "真实玩家评论研究所", font=font_sm, fill=ACCENT_COLOR)

    # ── Title ────────────────────────────────────────────────
    draw.text((60, 58), page.get("title", "折扣价格分析"), font=font_h, fill=TEXT_PRIMARY)
    draw.rectangle([60, 130, IMG_W - 60, 133], fill=BORDER_COLOR)

    data = page.get("data", {})
    current   = data.get("current_price")
    low       = data.get("historic_low")
    pct_above = data.get("pct_above_low")
    is_at_low = data.get("is_at_historic_low", False)
    discount  = data.get("discount_pct")
    orig      = data.get("original_price")

    y = 160

    # ── Price bars (visual comparison) ───────────────────────
    if orig is not None and current is not None:
        BAR_LEFT = 60
        BAR_MAX  = IMG_W - 200
        orig_w = BAR_MAX
        cur_w  = max(int(orig_w * (current / orig)), 20) if orig > 0 else BAR_MAX

        # Original price bar (dark neutral)
        draw.text((BAR_LEFT, y), f"原价 ${orig:.2f}", font=font_body, fill=TEXT_SECONDARY)
        y += 36
        draw.rectangle([BAR_LEFT, y, BAR_LEFT + orig_w, y + 34], fill="#2D2D3A")
        y += 50

        # Current price bar (amber — price semantic color)
        draw.text((BAR_LEFT, y), f"折后价 ${current:.2f}", font=font_body, fill=TEXT_PRIMARY)
        if discount:
            draw.text((BAR_LEFT + cur_w + 12, y), f"-{discount}%", font=font_body, fill=COLOR_PRICE)
        y += 36
        draw.rectangle([BAR_LEFT, y, BAR_LEFT + cur_w, y + 34], fill=COLOR_PRICE)
        y += 58

        # Historic low bar (green)
        if low is not None:
            low_w = max(int(orig_w * (low / orig)), 10) if orig > 0 else 20
            draw.text((BAR_LEFT, y), f"历史最低 ${low:.2f}", font=font_body, fill=TEXT_PRIMARY)
            y += 36
            draw.rectangle([BAR_LEFT, y, BAR_LEFT + low_w, y + 34], fill=COLOR_POSITIVE)
            y += 58

    # ── Verdict label ─────────────────────────────────────────
    y += 12
    if is_at_low:
        verdict_color = COLOR_POSITIVE
        verdict = "接近历史最低价 ✓"
    elif pct_above is not None:
        if pct_above <= 20:
            verdict_color = COLOR_PRICE
            verdict = f"高于史低 {pct_above:.0f}%，价格尚可"
        else:
            verdict_color = ACCENT_COLOR
            verdict = f"高于史低 {pct_above:.0f}%，可继续等"
    else:
        verdict_color = TEXT_SECONDARY
        verdict = "无历史价格数据"

    draw.text((60, y), verdict, font=font_sub, fill=verdict_color)
    y += 56

    # ── Body text ────────────────────────────────────────────
    y += 12
    draw_wrapped(draw, page.get("body", ""), font_body, TEXT_SECONDARY,
                 x=60, y=y, max_width=960, line_spacing=10)

    # ── Bottom accent strip ───────────────────────────────────
    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)

"""Page 6 — Review quotes card (Pillow)."""

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
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
)

if TYPE_CHECKING:
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.domain.games.entity import GameEntity


def render_review_card(
    entity: "GameEntity",
    theme_summary: "ThemeSummary | None",
    out_path: Path,
) -> Path:
    """Render up to 4 review quote bubbles and save PNG."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (IMG_W, IMG_H), color=CARD_BG)
    draw = ImageDraw.Draw(img)

    try:
        font_h = ImageFont.truetype(FONT_BOLD or "Arial", 42) if FONT_BOLD else ImageFont.load_default()
        font_body = ImageFont.truetype(FONT_REGULAR or "Arial", 27) if FONT_REGULAR else ImageFont.load_default()
        font_tag = ImageFont.truetype(FONT_REGULAR or "Arial", 20) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_h = font_body = font_tag = ImageFont.load_default()

    # top strip
    draw.rectangle([0, 0, IMG_W, 14], fill=ACCENT_COLOR)

    # title
    draw.text((60, 52), "玩家原话", font=font_h, fill=TEXT_PRIMARY)
    draw.text((60, 112), f"— {entity.name or ''} Steam 近期评论精选", font=font_tag, fill=TEXT_SECONDARY)
    draw.rectangle([60, 152, IMG_W - 60, 155], fill=BORDER_COLOR)

    # collect quotes
    quotes: list[tuple[str, str]] = []  # (text, sentiment)
    if theme_summary and theme_summary.themes:
        for t in sorted(theme_summary.themes, key=lambda x: -x.negative_count)[:4]:
            if t.sample_quote:
                sentiment = "差评" if t.negative_count > 0 else "好评"
                quotes.append((t.sample_quote[:90], f"{t.theme} · {sentiment}"))

    if not quotes and entity.sample_recent_review_excerpts:
        for q in entity.sample_recent_review_excerpts[:4]:
            quotes.append((str(q)[:90], "玩家评论"))

    if not quotes:
        quotes = [("暂无评论数据", "—")]

    # draw bubbles — dark-themed backgrounds
    y = 185
    bubble_pad = 26
    bubble_gap = 24
    bubble_colors = ["#1E1A14", "#141E14", "#1E1414", "#14181E"]  # dark amber/green/red/blue

    for i, (text, tag) in enumerate(quotes[:4]):
        bubble_color = bubble_colors[i % len(bubble_colors)]
        lines = _wrap_text(text, font_body, IMG_W - 60 - 60 - bubble_pad * 2)
        line_h = 38
        bubble_h = bubble_pad * 2 + len(lines) * line_h + 34

        draw.rounded_rectangle(
            [60, y, IMG_W - 60, y + bubble_h],
            radius=16,
            fill=bubble_color,
            outline=BORDER_COLOR,
            width=1,
        )

        ty = y + bubble_pad
        for line in lines:
            draw.text((60 + bubble_pad, ty), line, font=font_body, fill=TEXT_PRIMARY)
            ty += line_h

        draw.text((60 + bubble_pad, ty + 6), f"# {tag}", font=font_tag, fill=TEXT_SECONDARY)

        y += bubble_h + bubble_gap
        if y > IMG_H - 90:
            break

    # bottom strip
    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)
    return out_path


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        try:
            bbox = font.getbbox(test)
            w = bbox[2] - bbox[0]
        except Exception:
            w = len(test) * 14
        if w > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [""]

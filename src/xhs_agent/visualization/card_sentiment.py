"""Sentiment card — HP-bar positive rate comparison + review quotes (Pillow).

  1. Two horizontal "HP bar" style progress bars (historical vs recent 7d)
     with % overlaid inside the bar
  2. Up to 3 review quote bubbles, each with a full-height emoji circle on the left
"""

from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from xhs_agent.visualization.base import (
    ACCENT_COLOR,
    BORDER_COLOR,
    CARD_BG,
    COLOR_NEGATIVE,
    COLOR_NEUTRAL,
    COLOR_POSITIVE,
    COLOR_PRICE,
    FONT_BOLD,
    FONT_REGULAR,
    IMG_H,
    IMG_W,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    draw_wrapped,
)

if TYPE_CHECKING:
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.domain.games.entity import GameEntity

# Layout constants
_BAR_LEFT    = 60
_BAR_RIGHT   = IMG_W - 60
_BAR_W       = _BAR_RIGHT - _BAR_LEFT   # 960px
_BAR_H       = 52
_BAR_RADIUS  = 10
_TRACK_COLOR = "#2D2D3A"

# Emoji circle gap between circle and bubble
_CIRCLE_GAP  = 12

# ── Twemoji color emoji loader ────────────────────────────────────────────────
_TWEMOJI_BASE = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72"
_EMOJI_CACHE_DIR = Path.home() / ".cache" / "xhs_agent" / "twemoji"

# emoji char → twemoji filename (without .png)
_TWEMOJI_CODE: dict[str, str] = {
    "😤": "1f624", "😡": "1f621", "💥": "1f4a5", "💸": "1f4b8",
    "🎮": "1f3ae", "🎨": "1f3a8", "⚖️": "2696",  "🈶": "1f236",
    "💰": "1f4b0", "🚫": "1f6ab", "🗺️": "1f5fa",  "😴": "1f634",
    "📉": "1f4c9", "😊": "1f60a", "💬": "1f4ac",
}


def _get_emoji_img(emoji_char: str, size: int):
    """Return a PIL RGBA image of the emoji at `size`×`size`, or None on failure."""
    from PIL import Image

    code = _TWEMOJI_CODE.get(emoji_char)
    if not code:
        return None
    # Try primary code, then toggle -fe0f suffix as fallback
    candidates = [code]
    if code.endswith("-fe0f"):
        candidates.append(code[:-5])
    else:
        candidates.append(code + "-fe0f")

    _EMOJI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        cache_path = _EMOJI_CACHE_DIR / f"{candidate}.png"
        if not cache_path.exists():
            url = f"{_TWEMOJI_BASE}/{candidate}.png"
            try:
                with urllib.request.urlopen(url, timeout=6) as resp:
                    cache_path.write_bytes(resp.read())
            except Exception:
                continue
        try:
            return Image.open(cache_path).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            continue
    return None


# ── Theme → emoji mapping (keys are substrings of theme names) ────────────────
_THEME_EMOJI: list[tuple[str, str]] = [
    ("性能",  "😤"),
    ("优化",  "😤"),
    ("服务器", "😡"),
    ("联机",  "😡"),
    ("Bug",  "💥"),
    ("崩溃",  "💥"),
    ("价格",  "💸"),
    ("内容量", "💸"),
    ("玩法",  "🎮"),
    ("手感",  "🎮"),
    ("剧情",  "🎨"),
    ("美术",  "🎨"),
    ("平衡",  "⚖️"),
    ("中文",  "🈶"),
    ("本地化", "🈶"),
    ("DLC",  "💰"),
    ("商业化", "💰"),
    ("反作弊", "🚫"),
    ("账号",  "🚫"),
    ("新手",  "🗺️"),
    ("UI",   "🗺️"),
    ("重复",  "😴"),
    ("后期",  "😴"),
    ("更新",  "📉"),
    ("好评",  "😊"),
]
_DEFAULT_EMOJI = "💬"

def _emoji_for_tag(tag: str) -> str:
    for keyword, emoji in _THEME_EMOJI:
        if keyword in tag:
            return emoji
    return _DEFAULT_EMOJI

def _circle_color_for_tag(tag: str) -> str:
    if "差评" in tag:
        return "#3D1A1A"
    if "好评" in tag:
        return "#1A3D1A"
    return "#1A1A2D"


def render_sentiment_card(
    entity: "GameEntity",
    page: dict,
    out_path: Path,
    theme_summary: "ThemeSummary | None" = None,
) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (IMG_W, IMG_H), CARD_BG)
    draw = ImageDraw.Draw(img)

    try:
        font_tag   = ImageFont.truetype(FONT_REGULAR or "Arial", 20) if FONT_REGULAR else ImageFont.load_default()
        font_title = ImageFont.truetype(FONT_BOLD    or "Arial", 42) if FONT_BOLD    else ImageFont.load_default()
        font_sub   = ImageFont.truetype(FONT_REGULAR or "Arial", 24) if FONT_REGULAR else ImageFont.load_default()
        font_pct   = ImageFont.truetype(FONT_BOLD    or "Arial", 30) if FONT_BOLD    else ImageFont.load_default()
        font_lbl   = ImageFont.truetype(FONT_REGULAR or "Arial", 26) if FONT_REGULAR else ImageFont.load_default()
        font_delta = ImageFont.truetype(FONT_BOLD    or "Arial", 30) if FONT_BOLD    else ImageFont.load_default()
        font_quote = ImageFont.truetype(FONT_REGULAR or "Arial", 26) if FONT_REGULAR else ImageFont.load_default()
        font_qtag  = ImageFont.truetype(FONT_REGULAR or "Arial", 20) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_tag = font_title = font_sub = font_pct = font_lbl = font_delta = font_quote = font_qtag = ImageFont.load_default()

    # ── top accent strip ──
    draw.rectangle([0, 0, IMG_W, 14], fill=ACCENT_COLOR)

    # ── brand tag ──
    draw.text((60, 24), "真实玩家评论研究所", font=font_tag, fill=ACCENT_COLOR)

    # ── title ──
    title_end = draw_wrapped(
        draw, page.get("key_message", "好评率对比"),
        font_title, TEXT_PRIMARY,
        x=60, y=56, max_width=960, line_spacing=8, max_lines=3,
    )

    # ── subtitle ──
    subtitle = page.get("subtitle", "")
    if subtitle:
        sub_y = max(title_end + 6, 160)
        draw.text((60, sub_y), subtitle, font=font_sub, fill=TEXT_SECONDARY)

    # ── separator ──
    sep_y = 220
    draw.rectangle([60, sep_y, IMG_W - 60, sep_y + 2], fill=BORDER_COLOR)

    # ── parse rates ──
    def _pct(v) -> float:
        if v is None:
            return 0.0
        try:
            s = str(v).replace("%", "").strip()
            f = float(s)
            return f * 100 if f <= 1.0 else f
        except ValueError:
            return 0.0

    hist   = _pct(entity.historical_positive_rate)
    recent = _pct(entity.recent_7d_positive_rate)
    delta  = recent - hist

    # Bar fill color for recent: green if stable/up, amber if small drop, red if large drop
    if delta >= -3:
        recent_color = COLOR_POSITIVE
    elif delta >= -10:
        recent_color = COLOR_PRICE
    else:
        recent_color = COLOR_NEGATIVE

    # ── HP bars ──
    bar_y = sep_y + 28

    # — Historical bar —
    draw.text((60, bar_y), "历史好评率", font=font_lbl, fill=TEXT_SECONDARY)
    bar_y += 40
    _draw_hp_bar(draw, bar_y, hist / 100, COLOR_POSITIVE)
    # % overlaid on bar: right-aligned, vertically centered
    pct_y = bar_y + (_BAR_H - 30) // 2
    draw.text((_BAR_RIGHT - 14, pct_y), f"{hist:.1f}%",
              font=font_pct, fill="white", anchor="ra")
    bar_y += _BAR_H + 36

    # — Recent 7d bar —
    delta_str = f"{'↑' if delta >= 0 else '↓'}{abs(delta):.1f}%"
    delta_color = COLOR_POSITIVE if delta >= 0 else COLOR_NEGATIVE
    lbl_text = "近7天好评率"
    draw.text((60, bar_y), lbl_text, font=font_lbl, fill=TEXT_SECONDARY)
    try:
        lbl_w = font_lbl.getbbox(lbl_text)[2] - font_lbl.getbbox(lbl_text)[0]
    except Exception:
        lbl_w = 170
    draw.text((60 + lbl_w + 16, bar_y + 2), delta_str, font=font_delta, fill=delta_color)
    bar_y += 40
    _draw_hp_bar(draw, bar_y, recent / 100, recent_color)
    # % overlaid on bar
    draw.text((_BAR_RIGHT - 14, pct_y := bar_y + (_BAR_H - 30) // 2), f"{recent:.1f}%",
              font=font_pct, fill="white", anchor="ra")
    bar_y += _BAR_H + 40

    # ── divider before quotes ──
    div_y = bar_y + 20
    draw.rectangle([60, div_y, IMG_W - 60, div_y + 2], fill=BORDER_COLOR)
    draw.text((60, div_y + 14), "玩家怎么说", font=font_lbl, fill=TEXT_SECONDARY)

    # ── collect quotes ──
    quotes: list[tuple[str, str]] = []
    if theme_summary and getattr(theme_summary, "success", False) and theme_summary.themes:
        for t in sorted(theme_summary.themes, key=lambda x: -x.negative_count)[:3]:
            if t.sample_quote:
                sentiment = "差评" if t.negative_count > t.positive_count else "好评"
                quotes.append((t.sample_quote[:90], f"{t.theme} · {sentiment}"))

    if not quotes:
        raw = getattr(entity, "sample_recent_review_excerpts", None) or []
        for q in raw[:3]:
            quotes.append((str(q)[:90], "玩家评论"))

    # ── quote bubbles with emoji circle ──
    qy = div_y + 60
    bubble_gap = 20
    bubble_pad = 22
    bubble_colors = ["#1E1A14", "#141E14", "#1E1414"]

    for i, (text, tag) in enumerate(quotes[:3]):
        emoji_char = _emoji_for_tag(tag)
        circle_fill = _circle_color_for_tag(tag)
        bubble_color = bubble_colors[i % len(bubble_colors)]

        # Calculate bubble height based on text
        bubble_text_w = _BAR_W - bubble_pad * 2 - 0  # will reduce below
        lines = _wrap(text, font_quote, _BAR_W - bubble_pad * 2)
        line_h = 36
        bh = bubble_pad * 2 + len(lines) * line_h + 30

        if qy + bh > IMG_H - 60:
            break

        # ── emoji circle (full height of bubble, left side) ──
        circle_x1 = 60
        circle_x2 = circle_x1 + bh
        draw.ellipse([circle_x1, qy, circle_x2, qy + bh], fill=circle_fill, outline=BORDER_COLOR, width=1)

        # Paste color Twemoji PNG centered in circle
        emoji_size = max(bh - 24, 24)
        emoji_img = _get_emoji_img(emoji_char, emoji_size)
        if emoji_img:
            ex = circle_x1 + (bh - emoji_size) // 2
            ey = qy + (bh - emoji_size) // 2
            img.paste(emoji_img, (ex, ey), emoji_img)
        else:
            # fallback: white text in circle center
            cx = (circle_x1 + circle_x2) // 2
            cy = qy + bh // 2
            try:
                draw.text((cx, cy), emoji_char, font=font_quote, fill="white", anchor="mm")
            except Exception:
                pass

        # ── text bubble (starts after circle) ──
        bubble_x1 = circle_x2 + _CIRCLE_GAP
        bubble_x2 = IMG_W - 60
        draw.rounded_rectangle(
            [bubble_x1, qy, bubble_x2, qy + bh],
            radius=14, fill=bubble_color, outline=BORDER_COLOR, width=1,
        )

        # Re-wrap with actual bubble width
        inner_w = bubble_x2 - bubble_x1 - bubble_pad * 2
        lines = _wrap(text, font_quote, inner_w)
        ty = qy + bubble_pad
        for line in lines:
            draw.text((bubble_x1 + bubble_pad, ty), line, font=font_quote, fill=TEXT_PRIMARY)
            ty += line_h
        draw.text((bubble_x1 + bubble_pad, ty + 4), f"# {tag}", font=font_qtag, fill=TEXT_SECONDARY)

        qy += bh + bubble_gap

    # ── source / bottom ──
    src = page.get("source", "数据来源：Steam")
    draw.text((60, IMG_H - 44), src, font=font_tag, fill=TEXT_MUTED)
    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)
    return out_path


def _draw_hp_bar(draw: "ImageDraw.ImageDraw", y: int, fraction: float, fill_color: str) -> None:
    draw.rounded_rectangle(
        [_BAR_LEFT, y, _BAR_RIGHT, y + _BAR_H],
        radius=_BAR_RADIUS, fill=_TRACK_COLOR,
    )
    fill_w = max(int(_BAR_W * min(fraction, 1.0)), _BAR_RADIUS * 2)
    draw.rounded_rectangle(
        [_BAR_LEFT, y, _BAR_LEFT + fill_w, y + _BAR_H],
        radius=_BAR_RADIUS, fill=fill_color,
    )


def _wrap(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        try:
            w = font.getbbox(test)[2] - font.getbbox(test)[0]
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

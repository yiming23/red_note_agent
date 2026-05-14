"""Shared style constants, font loading, and chart card compositor."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────
# Canvas — 3:4 vertical for XHS carousel
# ─────────────────────────────────────────────────────────────
IMG_W = 1080
IMG_H = 1440
DPI   = 150   # 7.2" × 9.6" at 150 DPI

# ─────────────────────────────────────────────────────────────
# Dark theme palette
# ─────────────────────────────────────────────────────────────
BG_COLOR      = "#0D1117"   # outer canvas (deep dark)
CARD_BG       = "#161B22"   # main card container
BORDER_COLOR  = "#30363D"   # card border / separator

TEXT_PRIMARY   = "#E6EDF3"  # primary text (bright white)
TEXT_SECONDARY = "#8B949E"  # secondary labels (grey-blue)
TEXT_MUTED     = "#484F58"  # muted / source attribution

ACCENT_COLOR  = "#E8533A"   # brand red (unchanged)
ACCENT_LIGHT  = "#3D1A14"   # dark red fill (was light red)

# Semantic data colors (consistent across all charts)
COLOR_PLAYER   = "#56CCF2"  # cyan  — online player count
COLOR_PRICE    = "#F2C94C"  # amber — price / discount
COLOR_POSITIVE = "#27AE60"  # green — positive reviews / good signal
COLOR_NEGATIVE = "#E8533A"  # red   — negative reviews / risk  (= ACCENT_COLOR)
COLOR_NEUTRAL  = "#8B949E"  # grey  — neutral / insufficient data

# Kept for backward compat
SAFE_GREEN  = COLOR_POSITIVE
WARN_YELLOW = COLOR_PRICE
DANGER_RED  = COLOR_NEGATIVE

# Rating grade colors
RATING_COLORS = {
    "A": "#27AE60",
    "B": "#8BC34A",
    "C": "#F2C94C",
    "D": "#FF9800",
    "E": "#E8533A",
}

RATING_LABELS = {
    "A": "现在可入",
    "B": "等首个大补丁",
    "C": "等 20-30% 折扣",
    "D": "只推荐特定玩家",
    "E": "暂时避雷",
}

# ─────────────────────────────────────────────────────────────
# Font resolution
# ─────────────────────────────────────────────────────────────
_ASSET_FONTS = Path(__file__).parent.parent.parent.parent / "assets" / "fonts"

_FONT_CANDIDATES_BOLD = [
    _ASSET_FONTS / "NotoSansCJKsc-Bold.otf",
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
]
_FONT_CANDIDATES_REGULAR = [
    _ASSET_FONTS / "NotoSansCJKsc-Regular.otf",
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
]


def _resolve(candidates: list[Path]) -> Optional[str]:
    for p in candidates:
        if p.exists():
            return str(p)
    return None


FONT_BOLD    = _resolve(_FONT_CANDIDATES_BOLD)
FONT_REGULAR = _resolve(_FONT_CANDIDATES_REGULAR)


def get_matplotlib_font_prop(size: int = 14, bold: bool = False):
    """Return a matplotlib FontProperties object for CJK text."""
    from matplotlib.font_manager import FontProperties

    path = FONT_BOLD if bold else FONT_REGULAR
    if path:
        return FontProperties(fname=path, size=size)
    return FontProperties(size=size)


def _register_cjk_font() -> str | None:
    path = FONT_REGULAR
    if not path:
        return None
    try:
        from matplotlib import font_manager as fm
        fe = fm.FontEntry(fname=path, name="XHS-CJK")
        fm.fontManager.ttflist.insert(0, fe)
        return "XHS-CJK"
    except Exception:
        return None


_CJK_FAMILY: str | None = None


def apply_base_style() -> None:
    """Apply global matplotlib rcParams for dark brand style (including CJK font)."""
    import matplotlib as mpl

    global _CJK_FAMILY
    if _CJK_FAMILY is None:
        _CJK_FAMILY = _register_cjk_font()

    params: dict = {
        "figure.facecolor":    CARD_BG,
        "axes.facecolor":      CARD_BG,
        "axes.edgecolor":      BORDER_COLOR,
        "axes.spines.top":     False,
        "axes.spines.right":   False,
        "axes.spines.left":    True,
        "axes.spines.bottom":  True,
        "grid.color":          BORDER_COLOR,
        "grid.linewidth":      0.6,
        "xtick.color":         TEXT_SECONDARY,
        "ytick.color":         TEXT_SECONDARY,
        "text.color":          TEXT_PRIMARY,
        "savefig.bbox":        "tight",
        "savefig.pad_inches":  0.0,
        "savefig.facecolor":   CARD_BG,
    }
    if _CJK_FAMILY:
        params["font.family"] = [_CJK_FAMILY, "DejaVu Sans", "sans-serif"]

    mpl.rcParams.update(params)


# ─────────────────────────────────────────────────────────────
# Chart card layout constants  (3:4  1080 × 1440)
# ─────────────────────────────────────────────────────────────
CHART_W = 960    # chart paste width  (= IMG_W - 2 × 60px margins)
CHART_H = 686    # chart paste height (was 556; extra space from taller canvas)

CHART_FIGSIZE = (CHART_W / DPI, CHART_H / DPI)   # (6.4, 4.57) inches

# Vertical layout bands
_HEADER_Y_END   = 220   # 0–220:  top strip + brand tag + title + subtitle
_CHART_PASTE_Y  = 224   # chart pasted here (4px gap after separator)
_FOOTER_Y_START = 920   # = 224 + 686 + 10
_BOTTOM_STRIP_Y = 1410  # bottom accent strip at y=1410


def draw_rounded_vbars(ax, x_positions, heights, width, colors, alpha=1.0, bottom=0):
    """Replace ax.bar() with FancyBboxPatch bars that have rounded top corners."""
    from matplotlib.patches import FancyBboxPatch
    for x, h, color in zip(x_positions, heights, colors):
        if h <= 0:
            continue
        r = min(width * 0.35, h * 0.45)
        patch = FancyBboxPatch(
            (x - width / 2, bottom - r * 2), width, h + r * 2,
            boxstyle=f"round,pad=0,rounding_size={r}",
            fc=color, ec="none", alpha=alpha, zorder=3, clip_on=True,
        )
        ax.add_patch(patch)


def draw_rounded_hbars(ax, y_positions, widths, height, colors, alpha=1.0, left=0):
    """Replace ax.barh() with FancyBboxPatch bars that have rounded right side."""
    from matplotlib.patches import FancyBboxPatch
    for y, w, color in zip(y_positions, widths, colors):
        if w <= 0:
            continue
        r = min(height * 0.35, w * 0.45)
        patch = FancyBboxPatch(
            (left - r * 2, y - height / 2), w + r * 2, height,
            boxstyle=f"round,pad=0,rounding_size={r}",
            fc=color, ec="none", alpha=alpha, zorder=3, clip_on=True,
        )
        ax.add_patch(patch)


def compose_chart_card(
    *,
    chart_bytes: bytes,
    key_message: str,
    subtitle: str = "",
    how_to_read: str = "",
    conclusion: str = "",
    insights: list | None = None,  # extra bullet points in footer
    source: str = "数据来源：Steam",
    badge: str = "",          # e.g. "▶ PRICE CHECK"
    page_indicator: str = "", # e.g. "3 / 7"
) -> "PIL.Image.Image":
    """Composite a 1080×1440 dark-theme chart card.

    Layout:
      y=0–14    top accent strip
      y=14–220  header: brand tag / key_message / subtitle
      y=220–224 separator
      y=224–910 chart (960×686)
      y=910–920 separator
      y=920–1410 footer: how_to_read / conclusion / source
      y=1410–1440 bottom accent strip
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        font_tag   = ImageFont.truetype(FONT_REGULAR or "Arial", 20) if FONT_REGULAR else ImageFont.load_default()
        font_badge = ImageFont.truetype(FONT_BOLD    or "Arial", 18) if FONT_BOLD    else ImageFont.load_default()
        font_title = ImageFont.truetype(FONT_BOLD    or "Arial", 42) if FONT_BOLD    else ImageFont.load_default()
        font_sub   = ImageFont.truetype(FONT_REGULAR or "Arial", 24) if FONT_REGULAR else ImageFont.load_default()
        font_label   = ImageFont.truetype(FONT_BOLD    or "Arial", 24) if FONT_BOLD    else ImageFont.load_default()
        font_body    = ImageFont.truetype(FONT_REGULAR or "Arial", 26) if FONT_REGULAR else ImageFont.load_default()
        font_insight = ImageFont.truetype(FONT_REGULAR or "Arial", 26) if FONT_REGULAR else ImageFont.load_default()
        font_src     = ImageFont.truetype(FONT_REGULAR or "Arial", 20) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_tag = font_badge = font_title = font_sub = font_label = font_body = font_insight = font_src = ImageFont.load_default()

    # ── outer canvas ──
    canvas = Image.new("RGB", (IMG_W, IMG_H), BG_COLOR)
    draw   = ImageDraw.Draw(canvas)

    # ── rounded card container ──
    draw.rounded_rectangle(
        [28, 8, IMG_W - 28, IMG_H - 8],
        radius=20, fill=CARD_BG, outline=BORDER_COLOR, width=1,
    )

    # ── top accent strip (inside card top) ──
    draw.rectangle([28, 8, IMG_W - 28, 22], fill=ACCENT_COLOR)
    draw.rounded_rectangle([28, 8, IMG_W - 28, 36], radius=20, fill=ACCENT_COLOR)

    # ── brand tag ──
    draw.text((60, 46), "真实玩家评论研究所", font=font_tag, fill=TEXT_PRIMARY)

    # ── badge (e.g. "▶ PRICE CHECK") — right-aligned next to brand tag ──
    if badge:
        draw.text((IMG_W - 60, 46), badge, font=font_badge, fill=ACCENT_COLOR, anchor="ra")

    # ── key_message title ──
    title_end_y = draw_wrapped(
        draw, key_message, font_title, TEXT_PRIMARY,
        x=60, y=84, max_width=960, line_spacing=8, max_lines=3,
    )

    # ── subtitle ──
    if subtitle:
        subtitle_y = max(title_end_y + 8, 172)
        draw.text((60, subtitle_y), subtitle, font=font_sub, fill=TEXT_SECONDARY)

    # ── separator below header ──
    draw.rectangle([60, _HEADER_Y_END, IMG_W - 60, _HEADER_Y_END + 2], fill=BORDER_COLOR)

    # ── paste chart ──
    chart_img = Image.open(io.BytesIO(chart_bytes)).convert("RGB")
    chart_img = chart_img.resize((CHART_W, CHART_H), Image.LANCZOS)
    canvas.paste(chart_img, (60, _CHART_PASTE_Y))

    # ── separator above footer ──
    draw.rectangle([60, _FOOTER_Y_START, IMG_W - 60, _FOOTER_Y_START + 2], fill=BORDER_COLOR)

    # ── footer ──
    fy = _FOOTER_Y_START + 20
    if how_to_read:
        draw.text((60, fy), "怎么读：", font=font_label, fill=TEXT_SECONDARY)
        draw.text((60 + 90, fy), how_to_read, font=font_body, fill=TEXT_SECONDARY)
        fy += 48
    if conclusion:
        draw.text((60, fy), "结论：", font=font_label, fill=TEXT_PRIMARY)
        fy_after = draw_wrapped(
            draw, conclusion, font_body, TEXT_PRIMARY,
            x=60 + 72, y=fy, max_width=888, line_spacing=8, max_lines=5,
        )
        fy = max(fy_after, fy + 48)
    if insights:
        for ins in insights:
            if fy + 38 > _BOTTOM_STRIP_Y - 50:
                break
            fy = draw_wrapped(
                draw, f"▸  {ins}", font_insight, TEXT_SECONDARY,
                x=60, y=fy, max_width=960, line_spacing=6, max_lines=2,
            )
            fy += 8
    if source:
        src_y = min(fy + 12, _BOTTOM_STRIP_Y - 40)
        draw.text((60, src_y), source, font=font_src, fill=TEXT_MUTED)

    # ── page indicator (right-aligned in footer) ──
    if page_indicator:
        draw.text(
            (IMG_W - 60, _BOTTOM_STRIP_Y - 35),
            page_indicator, font=font_src, fill=TEXT_MUTED, anchor="ra",
        )

    # ── bottom accent strip ──
    draw.rounded_rectangle(
        [28, _BOTTOM_STRIP_Y, IMG_W - 28, IMG_H - 8],
        radius=20, fill=ACCENT_COLOR,
    )

    return canvas


def draw_wrapped(
    draw: "ImageDraw.ImageDraw",
    text: str,
    font,
    color: str,
    *,
    x: int,
    y: int,
    max_width: int,
    line_spacing: int = 8,
    max_lines: int = 999,
) -> int:
    """Draw CJK-aware wrapped text. Returns the y coordinate after the last line."""
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == '\n':
            if current:
                lines.append(current)
                if len(lines) >= max_lines:
                    break
            current = ""
            continue
        test = current + ch
        try:
            w = font.getbbox(test)[2] - font.getbbox(test)[0]
        except Exception:
            w = len(test) * 14
        if w > max_width and current:
            lines.append(current)
            if len(lines) >= max_lines:
                break
            current = ch
        else:
            current = test
    if current and len(lines) < max_lines:
        lines.append(current)

    cy = y
    for line in lines:
        draw.text((x, cy), line, font=font, fill=color)
        try:
            line_h = font.getbbox(line)[3] - font.getbbox(line)[1]
        except Exception:
            line_h = 20
        cy += line_h + line_spacing
    return cy

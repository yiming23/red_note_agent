"""Page 1 — Cover poster (Pillow)."""

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
    draw_wrapped,
)

if TYPE_CHECKING:
    from xhs_agent.agents.buy_or_wait import BuyRecommendation
    from xhs_agent.domain.games.entity import GameEntity


def render_cover(
    entity: "GameEntity",
    buy_rec: "BuyRecommendation",
    out_path: Path,
    title: str = "",
) -> Path:
    """Render a cover card and save to out_path. Returns out_path."""
    from PIL import Image

    appid = getattr(entity, "appid", None)
    steam_img = _try_fetch_steam_image(appid) if appid else None

    if steam_img:
        img = _render_layered_cover(entity, buy_rec, title, steam_img)
    else:
        img = _render_text_cover(entity, buy_rec, title)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)
    return out_path


def _try_fetch_steam_image(appid: str):
    try:
        from xhs_agent.visualization.steam_assets import fetch_cover_image
        return fetch_cover_image(appid)
    except Exception:
        return None


def _render_layered_cover(entity, buy_rec, title: str, steam_img_path: Path):
    """Build the layered cover: blurred Steam image + gradient + text."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    # ── 1. Load and crop/resize Steam image to 1080×1440 ──
    raw = Image.open(steam_img_path).convert("RGB")
    raw = _center_crop_canvas(raw)

    # ── 2. Blur + darken strongly for dark theme ──
    blurred = raw.filter(ImageFilter.GaussianBlur(radius=10))
    darkened = blurred.point(lambda p: int(p * 0.40))

    # ── 3. Vertical gradient overlay (transparent top → 90% black at bottom 50%) ──
    gradient = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    start_y = int(IMG_H * 0.50)
    for y in range(start_y, IMG_H):
        alpha = int(230 * (y - start_y) / (IMG_H - start_y))
        grad_draw.line([(0, y), (IMG_W, y)], fill=(0, 0, 0, alpha))

    canvas = Image.new("RGB", (IMG_W, IMG_H))
    canvas.paste(darkened)
    canvas.paste(Image.new("RGB", (IMG_W, IMG_H), (0, 0, 0)), mask=gradient.split()[3])

    draw = ImageDraw.Draw(canvas)

    # ── fonts ──
    try:
        font_tag   = ImageFont.truetype(FONT_REGULAR or "Arial", 22) if FONT_REGULAR else ImageFont.load_default()
        font_name  = ImageFont.truetype(FONT_BOLD    or "Arial", 60) if FONT_BOLD    else ImageFont.load_default()
        font_hook  = ImageFont.truetype(FONT_REGULAR or "Arial", 36) if FONT_REGULAR else ImageFont.load_default()
        font_badge = ImageFont.truetype(FONT_BOLD    or "Arial", 80) if FONT_BOLD    else ImageFont.load_default()
        font_lbl   = ImageFont.truetype(FONT_REGULAR or "Arial", 22) if FONT_REGULAR else ImageFont.load_default()
        font_sent  = ImageFont.truetype(FONT_REGULAR or "Arial", 30) if FONT_REGULAR else ImageFont.load_default()
        font_brand = ImageFont.truetype(FONT_REGULAR or "Arial", 20) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_tag = font_name = font_hook = font_badge = font_lbl = font_sent = font_brand = ImageFont.load_default()

    # ── top accent strip ──
    draw.rectangle([0, 0, IMG_W, 14], fill=ACCENT_COLOR)

    # ── brand tag pill ──
    tag_txt = "真实玩家评论研究所"
    tw = _text_width(draw, tag_txt, font_tag)
    pad = 14
    draw.rounded_rectangle([60, 28, 60 + tw + pad * 2, 28 + 36], radius=6, fill=ACCENT_COLOR)
    draw.text((60 + pad, 28 + 8), tag_txt, font=font_tag, fill="white")

    # ── game name ──
    game_name = f"《{entity.name or '未知游戏'}》"
    draw_wrapped(draw, game_name, font_name, "white",
                 x=60, y=140, max_width=960, line_spacing=10, max_lines=2)

    # ── hook title ──
    if title:
        draw_wrapped(draw, title, font_hook, "white",
                     x=60, y=320, max_width=960, line_spacing=10, max_lines=6)

    # ── rating badge (lower section, 3:4 gives more room) ──
    rating = buy_rec.rating if buy_rec and buy_rec.success else "C"
    rating_color = RATING_COLORS.get(rating, ACCENT_COLOR)
    rating_label = RATING_LABELS.get(rating, rating)

    bx, by = 60, 1060
    draw.rounded_rectangle([bx, by, bx + 160, by + 160], radius=16, fill=rating_color)
    draw.text((bx + 80, by + 28), rating, font=font_badge, fill="white", anchor="mt")
    draw.text((bx + 80, by + 122), rating_label, font=font_lbl, fill="white", anchor="mt")

    # ── one-sentence verdict ──
    if buy_rec and buy_rec.one_sentence:
        draw_wrapped(draw, buy_rec.one_sentence, font_sent, "white",
                     x=bx + 180, y=by + 50, max_width=800, line_spacing=10, max_lines=3)

    # ── brand mark bottom-right ──
    brand_txt = "真实玩家评论研究所"
    bw = _text_width(draw, brand_txt, font_brand)
    draw.text((IMG_W - 60 - bw, IMG_H - 50), brand_txt, font=font_brand, fill="#AAAAAA")

    # ── bottom accent strip ──
    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)

    return canvas


def _render_text_cover(entity, buy_rec, title: str):
    """Dark text-only cover fallback (no Steam image)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (IMG_W, IMG_H), color=CARD_BG)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype(FONT_BOLD    or "Arial", 52) if FONT_BOLD    else ImageFont.load_default()
        font_sub   = ImageFont.truetype(FONT_REGULAR or "Arial", 32) if FONT_REGULAR else ImageFont.load_default()
        font_tag   = ImageFont.truetype(FONT_BOLD    or "Arial", 38) if FONT_BOLD    else ImageFont.load_default()
        font_small = ImageFont.truetype(FONT_REGULAR or "Arial", 24) if FONT_REGULAR else ImageFont.load_default()
    except OSError:
        font_title = font_sub = font_tag = font_small = ImageFont.load_default()

    draw.rectangle([0, 0, IMG_W, 14], fill=ACCENT_COLOR)
    draw.text((60, 52), "真实玩家评论研究所", font=font_small, fill=ACCENT_COLOR)

    game_name = entity.name or "未知游戏"
    draw_wrapped(draw, game_name, font_title, TEXT_PRIMARY, x=60, y=130, max_width=960, line_spacing=12)

    draw.rectangle([60, 320, IMG_W - 60, 323], fill=BORDER_COLOR)

    display_title = title or ""
    draw_wrapped(draw, display_title, font_sub, TEXT_SECONDARY, x=60, y=348, max_width=960, line_spacing=10)

    rating = buy_rec.rating if buy_rec and buy_rec.success else "C"
    rating_color = RATING_COLORS.get(rating, ACCENT_COLOR)
    rating_label = RATING_LABELS.get(rating, rating)

    bx, by = 60, 960
    draw.rounded_rectangle([bx, by, bx + 180, by + 180], radius=20, fill=rating_color)
    draw.text((bx + 90, by + 50), rating, font=font_title, fill="white", anchor="mt")
    draw.text((bx + 90, by + 130), "级", font=font_small, fill="white", anchor="mt")
    draw.text((bx + 210, by + 30), rating_label, font=font_tag, fill=TEXT_PRIMARY)

    if buy_rec and buy_rec.one_sentence:
        draw_wrapped(draw, buy_rec.one_sentence, font_sub, TEXT_SECONDARY,
                     x=bx + 210, y=by + 90, max_width=740, line_spacing=8)

    draw.rectangle([60, IMG_H - 90, IMG_W - 60, IMG_H - 88], fill=BORDER_COLOR)
    stats = f"Steam评论数据 · 好评率 {entity.historical_positive_rate or '—'} · 评论数 {entity.total_reviews or '—'}"
    draw.text((60, IMG_H - 68), stats, font=font_small, fill=TEXT_MUTED)

    draw.rectangle([0, IMG_H - 14, IMG_W, IMG_H], fill=ACCENT_COLOR)
    return img


def _center_crop_canvas(img) -> "Image.Image":
    """Resize then center-crop to IMG_W × IMG_H."""
    from PIL import Image

    w, h = img.size
    scale = max(IMG_W / w, IMG_H / h)
    new_w, new_h = int(w * scale), int(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - IMG_W) // 2
    top  = (new_h - IMG_H) // 2
    return img.crop((left, top, left + IMG_W, top + IMG_H))


# Legacy alias
def _center_crop_1080(img) -> "Image.Image":
    return _center_crop_canvas(img)


def _text_width(draw, text: str, font) -> int:
    try:
        return font.getbbox(text)[2] - font.getbbox(text)[0]
    except Exception:
        return len(text) * 14


# Keep _draw_wrapped as a module-level alias so renderer.py can still import it
def _draw_wrapped(draw, text, font, color, x, y, max_width, line_spacing=8) -> int:
    return draw_wrapped(draw, text, font, color,
                        x=x, y=y, max_width=max_width, line_spacing=line_spacing)

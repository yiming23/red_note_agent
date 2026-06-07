"""Per-language/region positive-rate comparison chart.

Horizontal bar chart: China region (schinese/tchinese) highlighted in accent
colour, other languages in neutral grey, sorted by positive rate descending.
Only meaningful when there's a clear spread between regions (gated upstream).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity

_MAX_ROWS = 6  # show top N languages by sample size

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


def render_language_region_chart(entity: "GameEntity", page: dict, out_path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from xhs_agent.visualization.base import (
        ACCENT_COLOR,
        BORDER_COLOR,
        CARD_BG,
        CHART_FIGSIZE,
        COLOR_NEUTRAL,
        DPI,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        apply_base_style,
        compose_chart_card,
        draw_rounded_hbars,
        get_matplotlib_font_prop,
    )

    rates: dict = entity.review_positive_rate_by_language or {}
    dist: dict = entity.review_language_dist or {}
    if not rates:
        raise ValueError("no review_positive_rate_by_language data")

    apply_base_style()
    fp_val   = get_matplotlib_font_prop(size=15, bold=True)
    fp_label = get_matplotlib_font_prop(size=14)
    fp_tick  = get_matplotlib_font_prop(size=13)

    rows = sorted(rates.items(), key=lambda kv: dist.get(kv[0], 0), reverse=True)[:_MAX_ROWS]
    # Display order: highest rate at top
    rows.sort(key=lambda kv: -kv[1])

    names  = [_LANG_LABELS_CN.get(lang, lang) for lang, _ in rows]
    values = [rate * 100 for _, rate in rows]
    colors = [ACCENT_COLOR if lang in _CN_LANGS else COLOR_NEUTRAL for lang, _ in rows]
    n = len(rows)

    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    ys = list(range(n))
    draw_rounded_hbars(ax, ys, values, height=0.6, colors=colors, alpha=0.9)

    for y, val, (lang, _) in zip(ys, values, rows):
        ax.text(val + 1.0, y, f"{val:.0f}%",
                va="center", ha="left", fontproperties=fp_val,
                color=ACCENT_COLOR if lang in _CN_LANGS else TEXT_PRIMARY)

    ax.set_yticks(ys)
    ax.set_yticklabels(names, fontproperties=fp_label, color=TEXT_PRIMARY)
    ax.set_xlabel("好评率 (%)", fontproperties=fp_tick, color=TEXT_SECONDARY)
    ax.set_xlim(0, 115)
    ax.set_ylim(-0.6, n - 0.4)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    for lbl in ax.get_xticklabels():
        lbl.set_fontproperties(fp_tick)
        lbl.set_color(TEXT_SECONDARY)

    ax.grid(axis="x", zorder=0, alpha=0.4, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(BORDER_COLOR)
    ax.spines["left"].set_color(BORDER_COLOR)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    card = compose_chart_card(
        chart_bytes=buf.read(),
        key_message=page.get("key_message", "各地区好评率对比"),
        subtitle=page.get("subtitle", "数据来源：Steam 评论语言分布"),
        how_to_read=page.get("how_to_read", "红色条=国区，灰色条=其他地区，按好评率排序"),
        conclusion=page.get("conclusion", ""),
        insights=page.get("insights", []),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(str(out_path), "PNG", optimize=True)
    return out_path

"""Page 5 — Playtime distribution horizontal bar chart (matplotlib + Pillow).

Horizontal layout so rounded corners are visible regardless of count scale.
Bars: short/mid/long negative counts (red) + long positive count (green).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.processors.playtime_buckets import PlaytimeBucketResult


def render_playtime_chart(buckets: "PlaytimeBucketResult", page: dict, out_path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from xhs_agent.visualization.base import (
        BORDER_COLOR,
        CARD_BG,
        CHART_FIGSIZE,
        DPI,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        apply_base_style,
        compose_chart_card,
        draw_rounded_hbars,
        get_matplotlib_font_prop,
    )

    apply_base_style()
    fp_val   = get_matplotlib_font_prop(size=15, bold=True)
    fp_label = get_matplotlib_font_prop(size=14)
    fp_tick  = get_matplotlib_font_prop(size=13)

    _NEG_COLOR = "#C0394A"
    _POS_COLOR = "#2E9E5E"

    # Build bar list: short/mid/long negative, then long positive (if any)
    rows: list[tuple[str, float, str]] = [
        ("短时 (<2h)  差评",  buckets.short_neg, _NEG_COLOR),
        ("中时 (2–20h) 差评", buckets.med_neg,   _NEG_COLOR),
        ("长时 (≥20h)  差评", buckets.long_neg,  _NEG_COLOR),
    ]
    if buckets.long_pos > 0:
        rows.append(("长时 (≥20h)  好评", buckets.long_pos, _POS_COLOR))

    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    colors = [r[2] for r in rows]
    n = len(rows)

    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    ys = list(range(n))
    draw_rounded_hbars(ax, ys, values, height=0.55, colors=colors, alpha=0.88)

    # Value labels at end of each bar
    max_val = max(values) if values else 1
    for y, val in zip(ys, values):
        ax.text(val + max_val * 0.02, y, str(int(val)),
                va="center", ha="left", fontproperties=fp_val, color=TEXT_PRIMARY)

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontproperties=fp_label, color=TEXT_PRIMARY)
    ax.set_ylim(-0.6, n - 0.4)
    ax.invert_yaxis()

    ax.set_xlim(0, max_val * 1.3)
    ax.grid(axis="x", zorder=0, alpha=0.4, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(BORDER_COLOR)
    ax.spines["left"].set_color(BORDER_COLOR)
    for lbl in ax.get_xticklabels():
        lbl.set_fontproperties(fp_tick)
        lbl.set_color(TEXT_SECONDARY)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    card = compose_chart_card(
        chart_bytes=buf.read(),
        key_message=page.get("key_message", "游玩时长分布"),
        subtitle=page.get("subtitle", f"样本：{buckets.total} 条有时长数据的评论"),
        how_to_read=page.get("how_to_read", ""),
        conclusion=page.get("conclusion", ""),
        insights=page.get("insights", []),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(str(out_path), "PNG", optimize=True)
    return out_path

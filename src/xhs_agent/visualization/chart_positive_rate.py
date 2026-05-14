"""Page 3 — Positive-rate comparison chart card (matplotlib + Pillow composition)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity


def render_positive_rate_chart(entity: "GameEntity", page: dict, out_path: Path) -> Path:
    """Render a chart card comparing historical vs. recent 7d positive rate."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    from xhs_agent.visualization.base import (
        BORDER_COLOR,
        CARD_BG,
        CHART_FIGSIZE,
        COLOR_NEGATIVE,
        COLOR_POSITIVE,
        DPI,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        apply_base_style,
        compose_chart_card,
        get_matplotlib_font_prop,
    )

    apply_base_style()

    fp_val = get_matplotlib_font_prop(size=22, bold=True)
    fp_label = get_matplotlib_font_prop(size=18)

    def _to_pct(v) -> float:
        if v is None:
            return 0.0
        try:
            s = str(v).replace("%", "").strip()
            f = float(s)
            return f * 100 if f <= 1.0 else f
        except ValueError:
            return 0.0

    hist = _to_pct(entity.historical_positive_rate)
    recent = _to_pct(entity.recent_7d_positive_rate)

    # Color: green if recent ≥ hist (improving), red if declining
    recent_color = COLOR_POSITIVE if recent >= hist else COLOR_NEGATIVE

    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    bars = ax.bar(
        [0, 1],
        [hist, recent],
        color=[COLOR_POSITIVE, recent_color],
        width=0.5,
        zorder=3,
    )
    ax.set_ylim(0, 115)
    ax.set_xlim(-0.6, 1.6)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xticks([])
    ax.grid(axis="y", zorder=0, alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(BORDER_COLOR)
    ax.spines["left"].set_color(BORDER_COLOR)

    for bar, val, lbl in zip(bars, [hist, recent], ["历史好评率", "近7天好评率"]):
        cx = bar.get_x() + bar.get_width() / 2
        ax.text(cx, val + 2.5, f"{val:.1f}%", ha="center", va="bottom",
                fontproperties=fp_val, color=TEXT_PRIMARY)
        ax.text(cx, -6, lbl, ha="center", va="top",
                fontproperties=fp_label, color=TEXT_SECONDARY)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    card = compose_chart_card(
        chart_bytes=buf.read(),
        key_message=page.get("key_message", "好评率对比"),
        subtitle=page.get("subtitle", ""),
        how_to_read=page.get("how_to_read", ""),
        conclusion=page.get("conclusion", ""),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(str(out_path), "PNG", optimize=True)
    return out_path

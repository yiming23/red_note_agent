"""Player count history line chart (SteamCharts data)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity


def render_player_history_chart(entity: "GameEntity", page: dict, out_path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import numpy as np

    from xhs_agent.visualization.base import (
        BORDER_COLOR,
        CARD_BG,
        CHART_FIGSIZE,
        COLOR_PLAYER,
        COLOR_POSITIVE,
        COLOR_PRICE,
        DPI,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        apply_base_style,
        compose_chart_card,
        get_matplotlib_font_prop,
    )

    records = entity.player_count_history
    if not records:
        raise ValueError("no player_count_history data")

    apply_base_style()
    fp_anno = get_matplotlib_font_prop(size=16, bold=True)
    fp_label = get_matplotlib_font_prop(size=14)
    fp_tick = get_matplotlib_font_prop(size=13)

    months = [r["month"] for r in records]
    peaks = [r["peak"] for r in records]
    n = len(months)

    # X axis: show at most ~12 tick labels evenly spaced
    step = max(1, n // 12)
    tick_indices = list(range(0, n, step))
    if (n - 1) not in tick_indices:
        tick_indices.append(n - 1)

    xs = list(range(n))
    peak_idx = peaks.index(max(peaks))

    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    # Shaded area under curve — cyan for player count
    ax.fill_between(xs, peaks, alpha=0.15, color=COLOR_PLAYER)

    # Main line
    ax.plot(xs, peaks, color=COLOR_PLAYER, linewidth=2.5, zorder=3)

    # Highlight peak point (amber/yellow)
    ax.scatter([peak_idx], [peaks[peak_idx]], color=COLOR_PRICE, s=80, zorder=5)

    # Highlight current (last) point (green)
    ax.scatter([n - 1], [peaks[-1]], color=COLOR_POSITIVE, s=60, zorder=5)

    # Peak annotation
    peak_val_k = f"{peaks[peak_idx] / 1000:.1f}K" if peaks[peak_idx] >= 1000 else str(peaks[peak_idx])
    ax.annotate(
        f"峰值 {peak_val_k}",
        xy=(peak_idx, peaks[peak_idx]),
        xytext=(peak_idx, peaks[peak_idx] * 1.08),
        ha="center", va="bottom",
        fontproperties=fp_anno,
        color=COLOR_PRICE,
    )

    # Current annotation (only if not same as peak)
    if peak_idx != n - 1:
        cur_val_k = f"{peaks[-1] / 1000:.1f}K" if peaks[-1] >= 1000 else str(peaks[-1])
        ax.annotate(
            f"现在 {cur_val_k}",
            xy=(n - 1, peaks[-1]),
            xytext=(n - 1 - 0.5, peaks[-1] * 1.08),
            ha="right", va="bottom",
            fontproperties=fp_anno,
            color=COLOR_POSITIVE,
        )

    # Axes formatting
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(0, max(peaks) * 1.25)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}K" if v >= 1000 else f"{v:.0f}")
    )
    ax.set_xticks(tick_indices)
    ax.set_xticklabels(
        [months[i][2:] for i in tick_indices],  # "2024-09" → "24-09"
        fontproperties=fp_tick,
        color=TEXT_SECONDARY,
        rotation=30,
        ha="right",
    )
    for label in ax.get_yticklabels():
        label.set_fontproperties(fp_tick)
        label.set_color(TEXT_SECONDARY)

    ax.grid(axis="y", zorder=0, alpha=0.4, linestyle="--")
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
        key_message=page.get("key_message", "在线人数历史"),
        subtitle=page.get("subtitle", "数据来源：SteamCharts"),
        how_to_read=page.get("how_to_read", ""),
        conclusion=page.get("conclusion", ""),
        insights=page.get("insights", []),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(str(out_path), "PNG", optimize=True)
    return out_path

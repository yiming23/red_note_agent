"""Price history step-line chart (ITAD data).

Shows Steam price change events as a step chart, with discount periods
highlighted in red so the reader can see when/how often the game goes on sale.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity


def render_price_history_chart(entity: "GameEntity", page: dict, out_path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from xhs_agent.visualization.base import (
        BORDER_COLOR,
        CARD_BG,
        CHART_FIGSIZE,
        COLOR_POSITIVE,
        COLOR_PRICE,
        DPI,
        TEXT_MUTED,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        apply_base_style,
        compose_chart_card,
        get_matplotlib_font_prop,
    )

    records = entity.price_history
    if not records:
        raise ValueError("no price_history data")

    apply_base_style()
    fp_val = get_matplotlib_font_prop(size=15, bold=True)
    fp_tick = get_matplotlib_font_prop(size=13)

    # Build step-chart data: extend each price until next event
    dates = [r["date"] for r in records]
    prices = [r["price"] for r in records]
    cuts = [r["cut"] for r in records]
    regular = records[0]["regular"]  # original price (same across events)

    # Always add a terminal point so the last segment is visible.
    # If the last event is already this month, extend one month ahead.
    from datetime import date
    today = date.today()
    today_str = today.strftime("%Y-%m")
    if dates[-1] >= today_str:
        # Push one month forward so last segment renders
        m, y = today.month, today.year
        end_str = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
    else:
        end_str = today_str
    dates.append(end_str)
    prices.append(prices[-1])
    cuts.append(cuts[-1])

    n = len(dates)
    xs = list(range(n))

    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    # Draw step segments — amber for discount periods, muted for full price
    for i in range(n - 1):
        color = COLOR_PRICE if cuts[i] > 0 else "#3A3A4A"
        ax.step([xs[i], xs[i + 1]], [prices[i], prices[i]], where="post",
                color=color, linewidth=3, solid_capstyle="butt")
        if cuts[i] > 0:
            ax.fill_between(
                [xs[i], xs[i + 1]], [prices[i], prices[i]], [regular, regular],
                step="post", alpha=0.20, color=COLOR_PRICE,
            )

    # Regular price reference line — stop at last data point, no overflow
    ax.hlines(regular, xmin=-0.3, xmax=xs[-1], color=TEXT_MUTED,
              linewidth=1.2, linestyle="--", zorder=1)
    ax.text(0.01, regular * 1.02, f"原价 ${regular:.2f}",
            ha="left", va="bottom", fontproperties=fp_val, color=TEXT_SECONDARY,
            transform=ax.get_yaxis_transform())

    # Current price label — plain text, no arrow line
    current_price = prices[-1]
    if current_price < regular * 0.99:
        ax.text(
            xs[-1] - 0.15, current_price + regular * 0.04,
            f"现价 ${current_price:.2f}",
            ha="right", va="bottom",
            fontproperties=fp_val,
            color=COLOR_PRICE,
        )

    # Historic low annotation (only if meaningfully lower than current price)
    low_price = min(prices)
    low_idx = prices.index(low_price)
    if low_price < regular and abs(low_price - current_price) > 0.5:
        ax.annotate(
            f"史低 ${low_price:.2f}",
            xy=(xs[low_idx], low_price),
            xytext=(xs[low_idx], max(low_price - regular * 0.14, 0.3)),
            ha="center", va="top",
            fontproperties=fp_val,
            color=COLOR_POSITIVE,
        )

    # X ticks: show date labels
    step = max(1, n // 8)
    tick_idx = list(range(0, n, step))
    if (n - 1) not in tick_idx:
        tick_idx.append(n - 1)
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([dates[i][2:] for i in tick_idx],
                       fontproperties=fp_tick, color=TEXT_SECONDARY,
                       rotation=30, ha="right")
    for lbl in ax.get_yticklabels():
        lbl.set_fontproperties(fp_tick)
        lbl.set_color(TEXT_SECONDARY)

    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:.0f}"))
    ax.set_xlim(-0.3, n - 1)
    ax.set_ylim(0, regular * 1.3)
    ax.grid(axis="y", zorder=0, alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(BORDER_COLOR)
    ax.spines["left"].set_color(BORDER_COLOR)

    # Inline legend — more spacing between items
    ax.text(0, -0.12, "■ 打折区间", transform=ax.transAxes,
            fontproperties=fp_tick, color=COLOR_PRICE, va="top")
    ax.text(0.28, -0.12, "— 原价", transform=ax.transAxes,
            fontproperties=fp_tick, color=TEXT_MUTED, va="top")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    card = compose_chart_card(
        chart_bytes=buf.read(),
        key_message=page.get("key_message", "价格历史"),
        subtitle=page.get("subtitle", "数据来源：IsThereAnyDeal"),
        how_to_read=page.get("how_to_read", ""),
        conclusion=page.get("conclusion", ""),
        insights=page.get("insights", []),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(str(out_path), "PNG", optimize=True)
    return out_path

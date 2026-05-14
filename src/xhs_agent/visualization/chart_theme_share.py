"""Page 4 — Review theme share horizontal bar chart card (matplotlib + Pillow)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.agents.review_miner import ThemeSummary


def render_theme_share_chart(theme_summary: "ThemeSummary", page: dict, out_path: Path) -> Path:
    """Render top negative themes as a horizontal bar chart card."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from xhs_agent.visualization.base import (
        ACCENT_LIGHT,
        BORDER_COLOR,
        CARD_BG,
        CHART_FIGSIZE,
        COLOR_NEGATIVE,
        DPI,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        apply_base_style,
        compose_chart_card,
        draw_rounded_hbars,
        get_matplotlib_font_prop,
    )

    apply_base_style()

    themes = sorted(theme_summary.themes, key=lambda t: -t.negative_count)[:7]
    labels = [t.theme for t in themes]
    neg_vals = [t.negative_count for t in themes]
    total_vals = [t.count for t in themes]

    fp_label = get_matplotlib_font_prop(size=15)
    fp_val = get_matplotlib_font_prop(size=13, bold=True)

    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    # Dark theme: rounded bars — dim for total, brand red for negative
    y_pos = list(range(len(labels)))
    draw_rounded_hbars(ax, y_pos, total_vals, height=0.6, colors=["#2D2D3A"] * len(y_pos), alpha=0.9)
    draw_rounded_hbars(ax, y_pos, neg_vals,   height=0.6, colors=[COLOR_NEGATIVE] * len(y_pos), alpha=0.85)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontproperties=fp_label, color=TEXT_PRIMARY)
    # Pad y limits so first and last bars aren't half-clipped
    ax.set_ylim(-0.6, len(labels) - 0.4)
    ax.invert_yaxis()

    max_val = max(total_vals) if total_vals else 1
    for i, (neg, total) in enumerate(zip(neg_vals, total_vals)):
        ax.text(total + max_val * 0.02, i, f"{neg}/{total}",
                va="center", fontproperties=fp_val, color=TEXT_SECONDARY)

    ax.set_xlim(0, max_val * 1.25)
    ax.grid(axis="x", zorder=0, alpha=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(BORDER_COLOR)
    ax.spines["bottom"].set_color(BORDER_COLOR)

    from matplotlib.patches import Patch
    from matplotlib.font_manager import FontProperties
    from xhs_agent.visualization.base import FONT_REGULAR as _FR
    fp_legend = FontProperties(fname=_FR or "", size=12) if _FR else FontProperties(size=12)
    ax.legend(handles=[Patch(fc="#2D2D3A", alpha=0.9, label="全部评论"),
                        Patch(fc=COLOR_NEGATIVE, alpha=0.85, label="差评")],
              prop=fp_legend, loc="lower right", frameon=False,
              labelcolor=TEXT_SECONDARY, facecolor=CARD_BG)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    card = compose_chart_card(
        chart_bytes=buf.read(),
        key_message=page.get("key_message", "差评主题分布"),
        subtitle=page.get("subtitle", ""),
        how_to_read=page.get("how_to_read", ""),
        conclusion=page.get("conclusion", ""),
        insights=page.get("insights", []),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(str(out_path), "PNG", optimize=True)
    return out_path

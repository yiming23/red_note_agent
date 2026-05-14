"""Similar games positive-rate comparison chart (SteamSpy genre data).

Horizontal bar chart: target game highlighted in accent colour,
peers in neutral grey, sorted by positive rate descending.
"""

from __future__ import annotations

import io
import urllib.request
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity

_MAX_NAME_LEN = 18  # truncate long game names on the chart

# In-process cache: appid str → Chinese name (or None if unavailable)
_ZH_NAME_CACHE: dict[str, str | None] = {}


def _get_zh_name(appid: str) -> str | None:
    """Try to fetch the Simplified Chinese name from Steam store API."""
    if appid in _ZH_NAME_CACHE:
        return _ZH_NAME_CACHE[appid]
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=schinese&filters=basic"
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
        name = data.get(appid, {}).get("data", {}).get("name")
        _ZH_NAME_CACHE[appid] = name
        return name
    except Exception:
        _ZH_NAME_CACHE[appid] = None
        return None


def render_similar_games_chart(entity: "GameEntity", page: dict, out_path: Path) -> Path:
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

    peers = entity.similar_games or []
    if not peers:
        raise ValueError("no similar_games data")

    apply_base_style()
    fp_val = get_matplotlib_font_prop(size=15, bold=True)
    fp_label = get_matplotlib_font_prop(size=14)
    fp_tick = get_matplotlib_font_prop(size=13)

    # Build combined list: target game + peers, sort by positive_rate desc
    target_rate = entity.historical_positive_rate or 0.0
    target_total = entity.total_reviews or 0
    all_games = [
        {
            "name": entity.name,
            "positive_rate": target_rate,
            "total_reviews": target_total,
            "is_target": True,
        }
    ] + [dict(g, is_target=False) for g in peers]

    all_games.sort(key=lambda x: -x["positive_rate"])
    # Limit display to 8 rows max
    all_games = all_games[:8]

    def _display_name(g: dict) -> str:
        raw = g["name"]
        appid = g.get("appid")
        if appid and not g.get("is_target"):
            zh = _get_zh_name(str(appid))
            if zh:
                raw = zh
        return raw[:_MAX_NAME_LEN] + "…" if len(raw) > _MAX_NAME_LEN else raw

    names = [_display_name(g) for g in all_games]
    rates = [g["positive_rate"] * 100 for g in all_games]
    colors = [ACCENT_COLOR if g["is_target"] else COLOR_NEUTRAL for g in all_games]

    n = len(all_games)
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(CARD_BG)
    ax.set_facecolor(CARD_BG)

    ys = list(range(n))
    draw_rounded_hbars(ax, ys, rates, height=0.6, colors=colors, alpha=0.9)

    # Value labels
    for y, rate, game in zip(ys, rates, all_games):
        ax.text(rate + 0.5, y, f"{rate:.1f}%",
                va="center", ha="left",
                fontproperties=fp_val,
                color=ACCENT_COLOR if game["is_target"] else TEXT_PRIMARY)

    ax.set_yticks(ys)
    ax.set_yticklabels(names, fontproperties=fp_label, color=TEXT_PRIMARY)
    ax.set_xlabel("好评率 (%)", fontproperties=fp_tick, color=TEXT_SECONDARY)
    ax.set_xlim(0, 115)
    # Pad y limits so first and last bars aren't half-clipped
    ax.set_ylim(-0.6, n - 0.4)
    ax.invert_yaxis()  # highest rate at top
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
        key_message=page.get("key_message", "同类游戏好评率对比"),
        subtitle=page.get("subtitle", "数据来源：SteamSpy"),
        how_to_read=page.get("how_to_read", ""),
        conclusion=page.get("conclusion", ""),
        insights=page.get("insights", []),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(str(out_path), "PNG", optimize=True)
    return out_path

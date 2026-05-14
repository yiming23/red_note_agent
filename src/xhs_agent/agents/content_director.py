"""Content Director — Haiku agent that plans what article to write and what charts to show.

Given a user's direction (game + writing angle) and available entity data,
produces a ContentPlan that drives the manual pipeline.

Cost target: ~$0.001 per call (Haiku, ~500 in / 150 out tokens).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from xhs_agent.config import settings
from xhs_agent.observability.llm_tracker import call_llm
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.models import LlmPurpose

log = get_logger(__name__)

# Charts that the pipeline can render — order matters for display
AVAILABLE_CHART_TYPES = [
    "rate_trend",       # 好评率趋势 (needs: hist + recent rates)
    "theme_share",      # 差评主题分布 (needs: review_miner theme_summary)
    "playtime_dist",    # 游玩时长分布 (needs: review_stats_pool)
    "price_history",    # 价格历史 (needs: price_history + is_on_special)
    "player_history",   # 在线人数历史 (needs: player_count_history ≥6 months)
    "similar_games",    # 同类对比 (needs: similar_games peers ≥3)
]

_TEMPLATES = {
    "negative_review_burst": "差评爆炸 — 近期差评爆发，分析原因和是否值得买",
    "comeback_game":         "口碑反转 — 老游戏近期好评回升，适合回坑",
    "hidden_gem":            "小众神作 — 低知名度高好评，挖掘推荐",
    "discount_worth_checking": "折扣值不值 — 当前折扣是否值得购买",
    "playtime_contrast":     "评论区反差 — 短时差评 vs 长时好评，分析上手门槛",
    "new_release_heat":      "新品爆款 — 新发售游戏热度分析",
}

DIRECTOR_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "article_template": {
            "type": "string",
            "enum": list(_TEMPLATES.keys()),
            "description": "Which article template to use",
        },
        "key_narrative": {
            "type": "string",
            "description": "≤50字 核心叙事角度，决定整篇文章的主论点",
        },
        "search_queries": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
            "description": "DuckDuckGo search queries to gather external opinions",
        },
        "charts_needed": {
            "type": "array",
            "items": {"type": "string", "enum": AVAILABLE_CHART_TYPES},
            "maxItems": 4,
            "description": "Which charts to include, in display order",
        },
        "buy_rec_context": {
            "type": "string",
            "description": "Hint for buy-or-wait agent about the narrative angle",
        },
    },
    "required": ["article_template", "key_narrative", "search_queries", "charts_needed"],
}

DIRECTOR_TOOL_NAME = "submit_content_plan"
DIRECTOR_TOOL_DESC = "Submit the content plan for this article."


@dataclass
class ContentPlan:
    article_template: str
    key_narrative: str
    search_queries: list[str]
    charts_needed: list[str]
    buy_rec_context: str = ""


def direct_content(
    game_name: str,
    user_direction: str,
    available_data: dict,
    external_article_text: Optional[str] = None,
    pipeline_run_id: Optional[int] = None,
) -> ContentPlan:
    """Call Haiku to produce a ContentPlan for a manual-triggered article.

    Args:
        game_name: Steam game name
        user_direction: user's writing direction (e.g. "差评爆炸 因为更新删内容")
        available_data: dict of bool flags — which entity fields are populated
        external_article_text: optional full text of an external article to reference
        pipeline_run_id: for cost tracking
    """
    data_lines = []
    for key, val in available_data.items():
        data_lines.append(f"  {key}: {val}")
    data_summary = "\n".join(data_lines) or "  (无可用数据)"

    templates_text = "\n".join(
        f"  - {name}: {desc}" for name, desc in _TEMPLATES.items()
    )

    charts_text = "\n".join(
        f"  - {c}" for c in AVAILABLE_CHART_TYPES
    )

    ext_article_section = ""
    if external_article_text:
        ext_article_section = (
            f"\n## 参考外媒文章（用户提供，可作为写作参考）\n"
            f"{external_article_text[:1500]}\n"
        )

    system = (
        "你是内容规划师，帮助『真实玩家评论研究所』决定写什么文章。"
        "根据用户指定的游戏和写作方向，以及可用数据，选择最合适的文章类型和数据图表。"
        "只选择现有数据支持的图表（available_data 里标注为 true 的）。"
        "charts_needed 顺序决定图片顺序，最多4张，与叙事最相关的排前面。"
        "search_queries 用于 DuckDuckGo 搜索，返回1-2条：1条英文、1条中文。"
    )

    user_message = f"""## 游戏
{game_name}

## 用户指定写作方向
{user_direction}
{ext_article_section}
## 可选文章模板
{templates_text}

## 可用数据（true=有数据，false=无数据）
{data_summary}

## 可选图表类型
{charts_text}

请根据用户方向和可用数据，规划最合适的文章内容。
调用 {DIRECTOR_TOOL_NAME} 工具提交规划结果。"""

    estimated_in = (len(system) + len(user_message)) // 3
    estimated_out = 200

    result = call_llm(
        purpose=LlmPurpose.OPINION_MINING,  # reuse for cost tracking
        model=settings.model_signal_judgment,  # Haiku
        messages=[{"role": "user", "content": user_message}],
        system=system,
        max_tokens=400,
        temperature=0.3,
        pipeline_run_id=pipeline_run_id,
        estimated_in_tokens=estimated_in,
        estimated_out_tokens=estimated_out,
        tool_schema=DIRECTOR_TOOL_SCHEMA,
        tool_name=DIRECTOR_TOOL_NAME,
        tool_description=DIRECTOR_TOOL_DESC,
    )

    plan = _parse_plan(result.text)
    log.info(
        "content_director_done",
        game=game_name,
        template=plan.article_template,
        charts=plan.charts_needed,
        cost_usd=round(result.cost_usd, 4),
    )
    return plan


def _parse_plan(text: str) -> ContentPlan:
    cleaned = text.strip()
    m = re.match(r"^```(?:json|JSON)?\s*\n(.*)\n```\s*$", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)

    try:
        data = json.loads(cleaned)
        template = data.get("article_template", "negative_review_burst")
        if template not in _TEMPLATES:
            template = "negative_review_burst"
        charts = [c for c in data.get("charts_needed", []) if c in AVAILABLE_CHART_TYPES][:4]
        queries = [str(q) for q in data.get("search_queries", [])][:3]
        return ContentPlan(
            article_template=template,
            key_narrative=(data.get("key_narrative") or "")[:50],
            search_queries=queries,
            charts_needed=charts,
            buy_rec_context=(data.get("buy_rec_context") or "")[:200],
        )
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("content_director_parse_failed", error=str(exc), preview=cleaned[:200])
        # Fallback plan
        return ContentPlan(
            article_template="negative_review_burst",
            key_narrative="近期评论分析",
            search_queries=[],
            charts_needed=["rate_trend"],
            buy_rec_context="",
        )

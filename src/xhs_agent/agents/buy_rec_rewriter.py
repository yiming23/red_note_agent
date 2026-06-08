"""Buy-rec rewriter — revise the subjective verdict (rating / one_sentence /
suitable_for / key_risks / ...) to match a user's rewrite feedback, while
staying anchored to the game's frozen objective facts.

Triggered alongside `rewrite_agent` when Yiming replies to a pushed candidate
with full-article rewrite feedback. The article text gets rewritten by
`rewrite_agent`; this module re-derives the *subjective* verdict so the
cover / combined-summary / recommendation cards can be re-rendered with a
verdict that's consistent with both (a) the new narrative direction and
(b) the real numbers — never inventing conclusions that contradict the data.

Reuses the same tool-schema / parsing approach as `buy_or_wait.analyze`.
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.agents.buy_or_wait import (
    _TOOL_DESC,
    _TOOL_NAME,
    _TOOL_SCHEMA,
    BuyRecommendation,
    _parse,
)
from xhs_agent.config import settings
from xhs_agent.observability.llm_tracker import call_llm
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.models import LlmPurpose

log = get_logger(__name__)

_SYSTEM = (
    "你是『真实玩家评论研究所』的消费建议分析员。"
    "用户对一篇已生成的文章提出了修改方向，文章正文已经按新方向重写。"
    "你的任务：在不违背下方『客观数据（真实、不可更改）』的前提下，"
    "重新给出与新文章论调一致的 A-E 级消费建议（rating / one_sentence / "
    "suitable_for / not_suitable_for / key_risks / wait_for）。"
    "禁止编造与客观数据矛盾的结论（例如数据显示好评率很高，就不能给出'避雷'类结论）；"
    "但叙事角度、措辞、侧重点应贴合用户的新方向。"
    "不使用'垃圾/必买/千万别买'等绝对化表达。调用工具输出结构化结果。"
)


def revise(
    *,
    original: BuyRecommendation,
    feedback: str,
    objective_facts: dict,
    game_name: str,
    revised_title: str,
    revised_content: str,
    pipeline_run_id: Optional[int] = None,
) -> BuyRecommendation:
    """Produce a revised BuyRecommendation consistent with new direction + real data.

    Falls back to returning `original` unchanged if the LLM call/parse fails —
    so a rewrite never ends up with a worse verdict than before.
    """
    context = _build_context(
        original=original,
        feedback=feedback,
        objective_facts=objective_facts,
        game_name=game_name,
        revised_title=revised_title,
        revised_content=revised_content,
    )

    result = call_llm(
        purpose=LlmPurpose.BUY_OR_WAIT,
        model=settings.model_signal_judgment,   # Haiku — cheap & structured
        messages=[{"role": "user", "content": context}],
        system=_SYSTEM,
        max_tokens=1000,
        temperature=0.2,
        pipeline_run_id=pipeline_run_id,
        estimated_in_tokens=len(context) // 3,
        estimated_out_tokens=300,
        tool_schema=_TOOL_SCHEMA,
        tool_name=_TOOL_NAME,
        tool_description=_TOOL_DESC,
    )

    if not result.success or not result.text:
        log.warning("buy_rec_revise_failed", game=game_name, error=result.error_message)
        return original

    rec = _parse(result.text)
    if not rec.success:
        log.warning("buy_rec_revise_parse_failed", game=game_name)
        return original

    rec.cost_usd = result.cost_usd
    log.info("buy_rec_revise_done", game=game_name,
             original_rating=original.rating, revised_rating=rec.rating,
             cost_usd=round(rec.cost_usd, 4))
    return rec


def _build_context(
    *,
    original: BuyRecommendation,
    feedback: str,
    objective_facts: dict,
    game_name: str,
    revised_title: str,
    revised_content: str,
) -> str:
    facts = objective_facts or {}
    lines: list[str] = [
        f"## 游戏：{game_name}",
        "\n## 客观数据（真实、不可更改 —— 新结论不能与之矛盾）",
        f"历史好评率：{facts.get('historical_positive_rate')}",
        f"近 7 天好评率：{facts.get('recent_7d_positive_rate')}",
        f"近 7 天评论数：{facts.get('recent_7d_review_count')}",
        f"总评论数：{facts.get('total_reviews')}",
        f"当前在线人数：{facts.get('current_player_count')}",
        "\n## 原结论",
        f"评级：{original.rating} — {original.one_sentence}",
        f"适合：{' / '.join(original.suitable_for) if original.suitable_for else '（无）'}",
        f"不适合：{' / '.join(original.not_suitable_for) if original.not_suitable_for else '（无）'}",
        f"风险：{' / '.join(original.key_risks) if original.key_risks else '（无）'}",
        f"等什么：{original.wait_for or '（无）'}",
        "\n## 用户提出的修改方向",
        feedback[:1500],
        "\n## 重写后的新文章",
        f"标题：{revised_title}",
        f"正文（节选）：{revised_content[:800]}",
        "\n## 任务",
        "结合『客观数据』和『新文章』的论调，重新给出 A-E 级消费建议。"
        "调用 submit_buy_recommendation 工具。",
    ]
    return "\n".join(lines)

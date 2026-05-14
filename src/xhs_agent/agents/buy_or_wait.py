"""Buy-or-Wait Analyst — produce a consumer recommendation rating for a game.

Input:  GameEntity + optional ThemeSummary + optional PlaytimeBucketResult
Output: BuyRecommendation  (A/B/C/D/E rating + suitable_for / key_risks / wait_for)

Uses Haiku via tool use for structured, low-cost output (~$0.003 per call).

DESIGN_v5.md §7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from xhs_agent.config import settings
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.llm_tracker import call_llm
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.models import LlmPurpose

if TYPE_CHECKING:
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.processors.playtime_buckets import PlaytimeBucketResult

log = get_logger(__name__)


# ────────────────────────────────────────────────────────────
# Output dataclass
# ────────────────────────────────────────────────────────────


@dataclass
class BuyRecommendation:
    rating: str                          # A / B / C / D / E
    one_sentence: str                    # 一句话结论
    suitable_for: list[str] = field(default_factory=list)
    not_suitable_for: list[str] = field(default_factory=list)
    key_risks: list[str] = field(default_factory=list)
    wait_for: str = ""                   # 等什么 (e.g. "首个大补丁" / "20% 折扣")
    cost_usd: float = 0.0
    success: bool = True
    error_message: Optional[str] = None

    def rating_label(self) -> str:
        labels = {
            "A": "现在可入",
            "B": "等首个大补丁",
            "C": "等 20-30% 折扣",
            "D": "只推荐粉丝/特定玩家",
            "E": "暂时避雷",
        }
        return labels.get(self.rating, self.rating)

    def format_for_telegram(self) -> str:
        lines = [f"🎯 购买建议：{self.rating} — {self.rating_label()}"]
        lines.append(f"📌 {self.one_sentence}")
        if self.suitable_for:
            lines.append("✅ 适合：" + " / ".join(self.suitable_for[:3]))
        if self.not_suitable_for:
            lines.append("❌ 不适合：" + " / ".join(self.not_suitable_for[:2]))
        if self.key_risks:
            lines.append("⚠️ 风险：" + " / ".join(self.key_risks[:2]))
        if self.wait_for:
            lines.append(f"⏳ 等：{self.wait_for}")
        return "\n".join(lines)

    def format_for_page(self) -> str:
        """Multi-line text for the recommendation page card (S7 Pillow render)."""
        lines = [
            f"【{self.rating}级】{self.rating_label()}",
            "",
            self.one_sentence,
            "",
        ]
        if self.suitable_for:
            lines.append("✅ 适合谁")
            lines.extend(f"  • {s}" for s in self.suitable_for[:4])
            lines.append("")
        if self.not_suitable_for:
            lines.append("❌ 不适合谁")
            lines.extend(f"  • {s}" for s in self.not_suitable_for[:3])
            lines.append("")
        if self.key_risks:
            lines.append("⚠️ 关键风险")
            lines.extend(f"  • {r}" for r in self.key_risks[:3])
            lines.append("")
        if self.wait_for:
            lines.append(f"⏳ 如果等的话：等 {self.wait_for}")
        return "\n".join(lines).strip()


# ────────────────────────────────────────────────────────────
# Tool schema
# ────────────────────────────────────────────────────────────

_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "rating": {
            "type": "string",
            "enum": ["A", "B", "C", "D", "E"],
            "description": "A=现在可入 B=等大补丁 C=等折扣 D=只推粉丝 E=暂时避雷",
        },
        "one_sentence": {
            "type": "string",
            "description": "一句话结论（25字内），不含'我'字，不使用绝对化表达",
        },
        "suitable_for": {
            "type": "array",
            "items": {"type": "string"},
            "description": "适合谁（2-4条，每条≤20字，描述玩家类型而非游戏特性）",
        },
        "not_suitable_for": {
            "type": "array",
            "items": {"type": "string"},
            "description": "不适合谁（1-3条）",
        },
        "key_risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "关键风险点（1-3条，基于评论数据，非猜测）",
        },
        "wait_for": {
            "type": "string",
            "description": "如果建议等，等什么（如'首个大补丁'/'20%折扣'/'内容更新'）。A级可留空。",
        },
    },
    "required": ["rating", "one_sentence", "suitable_for", "not_suitable_for", "key_risks", "wait_for"],
}

_TOOL_NAME = "submit_buy_recommendation"
_TOOL_DESC = "Submit the consumer buy-or-wait recommendation for this game."


# ────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────


def analyze(
    entity: GameEntity,
    *,
    theme_summary: Optional["ThemeSummary"] = None,
    playtime_buckets: Optional["PlaytimeBucketResult"] = None,
    pipeline_run_id: Optional[int] = None,
) -> BuyRecommendation:
    """Produce a BuyRecommendation for `entity`.

    Falls back to a heuristic E-grade recommendation if the LLM call fails,
    so the pipeline never blocks on this agent.
    """
    context = _build_context(entity, theme_summary, playtime_buckets)

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
        log.warning("buy_or_wait_failed", appid=entity.appid, error=result.error_message)
        return BuyRecommendation(
            rating="C",
            one_sentence="数据不足，建议先观望",
            wait_for="等首批玩家反馈",
            cost_usd=result.cost_usd,
            success=False,
            error_message=result.error_message,
        )

    rec = _parse(result.text)
    rec.cost_usd = result.cost_usd

    # Hard guard: don't assign E to games with insufficient review data.
    # E means "avoid" which requires actual negative evidence — not just absence of reviews.
    if (entity.total_reviews or 0) < 50 and rec.rating == "E":
        log.info("buy_or_wait_rating_overridden", appid=entity.appid,
                 original="E", overridden_to="C", reason="insufficient_reviews")
        rec.rating = "C"
        rec.wait_for = rec.wait_for or "等首批玩家反馈"

    log.info("buy_or_wait_done", appid=entity.appid, rating=rec.rating,
             cost_usd=round(rec.cost_usd, 4))
    return rec


# ────────────────────────────────────────────────────────────
# Internals
# ────────────────────────────────────────────────────────────

_SYSTEM = (
    "你是『真实玩家评论研究所』的消费建议分析员。"
    "根据 Steam 评论数据给出 A-E 级消费建议。"
    "结论要基于数据，不能凭感觉。不使用'垃圾/必买/千万别买'等绝对化表达。"
    "调用工具输出结构化结果。"
)


def _build_context(
    entity: GameEntity,
    theme_summary: Optional["ThemeSummary"],
    playtime_buckets: Optional["PlaytimeBucketResult"],
) -> str:
    lines: list[str] = [
        f"## 游戏：{entity.name} (appid={entity.appid})",
        f"上架天数：{entity.game_age_days}",
        f"历史好评率：{entity.historical_positive_rate}",
        f"近 7 天好评率：{entity.recent_7d_positive_rate}",
        f"近 7 天评论数：{entity.recent_7d_review_count}",
        f"总评论数：{entity.total_reviews}",
        f"当前在线人数：{entity.current_player_count}",
    ]

    if entity.is_on_special and entity.discount_pct:
        lines.append(f"当前折扣：{entity.discount_pct}% off（原价 ${entity.original_price}，现价 ${entity.final_price}）")
        if entity.historic_low_price is not None:
            if entity.is_at_historic_low:
                lines.append(f"价格状态：接近历史最低（史低 ${entity.historic_low_price}）")
            else:
                lines.append(f"价格状态：高于史低 {entity.pct_above_historic_low:.1f}%（史低 ${entity.historic_low_price}）")

    if theme_summary and theme_summary.success and theme_summary.themes:
        lines.append("\n## 差评主题（Review Miner）")
        for t in sorted(theme_summary.themes, key=lambda x: -x.negative_count)[:5]:
            if t.negative_count > 0:
                lines.append(f"- {t.theme}: 差评 {t.negative_count} / 总 {t.count}（{t.share_pct}%）")
                if t.sample_quote:
                    lines.append(f"  代表：「{t.sample_quote}」")

    if playtime_buckets and playtime_buckets.total > 0:
        lines.append("\n## 游玩时长分组")
        lines.append(playtime_buckets.format_for_prompt())

    if entity.sample_recent_review_excerpts:
        lines.append("\n## 玩家原话片段")
        for q in entity.sample_recent_review_excerpts[:3]:
            lines.append(f"- {q[:120]}")

    # Inject prelaunch constraint when review data is insufficient
    if (entity.recent_7d_review_count or 0) < 10 and (entity.total_reviews or 0) < 50:
        lines.append("\n## ⚠️ 数据说明")
        lines.append("该游戏近期评论极少，没有足够的玩家口碑数据支撑负面结论。")
        lines.append("禁止给 E 级（暂时避雷需要有差评证据）。")
        lines.append("请用 C 或 D 级，wait_for 填 '等首批玩家反馈'。")
        lines.append("one_sentence 应体现数据不足，如'热度在，口碑待验证，等首批反馈再决定'。")

    lines.append("\n## 任务")
    lines.append("基于以上数据，给出 A-E 级消费建议。调用 submit_buy_recommendation 工具。")
    return "\n".join(lines)


def _parse(text: str) -> BuyRecommendation:
    import json
    import re

    text = text.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("buy_or_wait_parse_failed", preview=text[:100])
        return BuyRecommendation(
            rating="C", one_sentence="解析失败，建议先观望", success=False, error_message="json_parse_error"
        )
    return BuyRecommendation(
        rating=str(data.get("rating", "C")).upper(),
        one_sentence=str(data.get("one_sentence", "")).strip(),
        suitable_for=list(data.get("suitable_for") or []),
        not_suitable_for=list(data.get("not_suitable_for") or []),
        key_risks=list(data.get("key_risks") or []),
        wait_for=str(data.get("wait_for") or "").strip(),
        success=True,
    )

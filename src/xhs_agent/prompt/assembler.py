"""Runtime prompt assembly — v5.

DESIGN_v5.md § 8. prompts are built per-call from:
  base_persona + template + signal context + preferred phrasings + (optional) exemplars

v5 changes vs v4:
- Persona is now "理性消费分析员" not "抽象博主"
- Title MUST include 《game_name》, length 16-30
- Hashtag count 5-10, mostly assembled by hashtag_policy module after LLM output
- Meme injection removed (memes table reserved for V2)
- Exemplars still supported but optional (理性研究所风格不依赖样本)
- New: banned_expressions list passed to LLM as hard "don't write this" guidance
- New: preferred_phrasings list passed to LLM as "write more like this" guidance
- New: opening + comment_prompt suggestions passed to LLM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from xhs_agent.config import tuning
from xhs_agent.domain.base import SignalResult, Template
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.repositories import ExemplarRepository

if TYPE_CHECKING:
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.processors.playtime_buckets import PlaytimeBucketResult

log = get_logger(__name__)

PERSONA_PATH = Path(__file__).resolve().parents[1] / "config" / "persona.yaml"


# ============================================================
# Loaded persona
# ============================================================


@dataclass
class Persona:
    account_name: str
    text: str
    title_min_chars: int
    title_max_chars: int
    content_target: str
    paragraphs_max: int
    hashtag_count: str   # display only; real enforcement via hashtag_policy
    title_patterns: dict[str, list[str]]   # by 模板组
    banned_expressions: dict[str, list[str]]
    preferred_phrasings: list[str] = field(default_factory=list)
    recommended_openings: list[str] = field(default_factory=list)
    recommended_comment_prompts: list[str] = field(default_factory=list)


_persona_cache: Optional[Persona] = None


def load_persona() -> Persona:
    global _persona_cache
    if _persona_cache is not None:
        return _persona_cache

    with PERSONA_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    fmt = data.get("format_rules", {})
    _persona_cache = Persona(
        account_name=str(data.get("account_name") or "真实玩家评论研究所"),
        text=str(data.get("persona", "")).strip(),
        title_min_chars=int(fmt.get("title_min_chars", 16)),
        title_max_chars=int(fmt.get("title_max_chars", 30)),
        content_target=str(fmt.get("content_target_chars", "250-450")),
        paragraphs_max=int(fmt.get("paragraphs_max", 6)),
        hashtag_count=str(fmt.get("hashtag_count", "5-10")),
        title_patterns=dict(data.get("title_patterns") or {}),
        banned_expressions=dict(data.get("banned_expressions") or {}),
        preferred_phrasings=list(data.get("preferred_phrasings") or []),
        recommended_openings=list(data.get("recommended_openings") or []),
        recommended_comment_prompts=list(data.get("recommended_comment_prompts") or []),
    )
    return _persona_cache


def reload_persona() -> Persona:
    """Force re-read persona.yaml (useful when editing in a running shell)."""
    global _persona_cache
    _persona_cache = None
    return load_persona()


# ============================================================
# Assembled prompt result
# ============================================================


@dataclass
class AssembledPrompt:
    system: str
    user_message: str
    used_meme_ids: list[int]      # kept for schema compat; v5 always []
    used_exemplar_ids: list[int]
    template_name: str


# ============================================================
# Public API — content generation
# ============================================================


def assemble_content_prompt(
    template: Template,
    signal: SignalResult,
    entity: GameEntity,
    angle_hint: Optional[str] = None,
    theme_summary: Optional["ThemeSummary"] = None,
    playtime_buckets: Optional["PlaytimeBucketResult"] = None,
    *,
    exemplars_k: Optional[int] = None,
    price_context: Optional[str] = None,
) -> AssembledPrompt:
    """Build prompts for content_agent (Sonnet).

    Args:
        template: which v5 content_type template to use
        signal: SignalResult that triggered this generation
        entity: GameEntity with reviews / metadata
        angle_hint: optional steer from signal_agent
        exemplars_k: how many few-shot exemplars to inject (None = use tuning)
    """
    persona = load_persona()

    if exemplars_k is None:
        exemplars_k = tuning.content.exemplars_per_template

    # Exemplars: optional in v5. Pull what's available; don't sweat if empty.
    used_exemplar_ids: list[int] = []
    exemplar_blocks: list[str] = []

    with session_scope() as s:
        for ex in ExemplarRepository(s).fetch_by_template(template.name, k=exemplars_k):
            tags = ex.style_tags or []
            tag_str = "，".join(tags) if tags else ""
            block = (
                f"【参考样本{f' / {tag_str}' if tag_str else ''}】\n"
                f"{ex.raw_content.strip()}"
            )
            exemplar_blocks.append(block)
            used_exemplar_ids.append(ex.id)

    system = _build_system(persona)
    user_message = _build_user_message(
        persona=persona,
        template=template,
        signal=signal,
        entity=entity,
        angle_hint=angle_hint,
        exemplar_blocks=exemplar_blocks,
        theme_summary=theme_summary,
        playtime_buckets=playtime_buckets,
        price_context=price_context,
    )

    return AssembledPrompt(
        system=system,
        user_message=user_message,
        used_meme_ids=[],
        used_exemplar_ids=used_exemplar_ids,
        template_name=template.name,
    )


# ============================================================
# Public API — rewrite
# ============================================================


def assemble_rewrite_prompt(
    original_title: str,
    original_content: str,
    original_hashtags: list[str],
    feedback: str,
    template: Optional[Template] = None,
    *,
    game_name: Optional[str] = None,
) -> AssembledPrompt:
    """Rewrite an existing post per user feedback. Keeps v5 hard constraints in place."""
    persona = load_persona()
    system = _build_system(persona)

    template_section = ""
    if template:
        template_section = (
            f"## 模板\n"
            f"content_type：{template.content_type}\n"
            f"语气：{template.tone}\n"
            f"结构：{', '.join(template.structure)}\n\n"
        )

    game_section = f"\n本游戏名：{game_name}（标题里必须含《{game_name}》）\n" if game_name else ""

    user_message = f"""
{template_section}## 原稿
标题：{original_title}
正文：
{original_content}
hashtag：{', '.join(original_hashtags) if original_hashtags else '(无)'}
{game_section}
## 用户反馈
{feedback.strip()}

## 任务
按用户反馈修改原稿。可以只改一部分，也可以重写。保留原稿的事实信息（数据、玩家原话）不要编造。

## v5 硬性规则（不可违反）
- 标题必须包含《游戏名》且字数 {persona.title_min_chars}-{persona.title_max_chars}
- 正文 {persona.content_target} 字
- hashtag {persona.hashtag_count} 个，至少包含 #Steam游戏 / #真实玩家评论 / #游戏值不值得买
- 禁止"垃圾游戏 / 千万别买 / 必买神作 / 闭眼入"等绝对化表达
- 保持"理性消费分析员"口吻，不要博主感

## 输出
调用 submit_post 工具，返回 title / content / hashtags。
"""
    return AssembledPrompt(
        system=system,
        user_message=user_message.strip(),
        used_meme_ids=[],
        used_exemplar_ids=[],
        template_name=template.name if template else "rewrite",
    )


# ============================================================
# Public API — signal judgment
# ============================================================


def assemble_signal_judgment_prompt(
    signals: list[SignalResult],
    entities_by_id: dict[str, GameEntity],
) -> AssembledPrompt:
    """Trend Scout (Haiku) prompt — score each signal worth_writing + recommend template."""
    lines: list[str] = ["## 候选信号清单\n"]
    for i, sig in enumerate(signals, start=1):
        ent = entities_by_id.get(sig.entity_id)
        ent_summary = ""
        if ent:
            quotes = " / ".join(ent.sample_recent_review_excerpts[:2])
            genres = ", ".join(ent.genres[:3]) if ent.genres else "?"
            ent_summary = (
                f"  类型: {genres} | 当前在线: {ent.current_player_count} | "
                f"7d 评论: {ent.recent_7d_review_count} | "
                f"7d 好评率: {ent.recent_7d_positive_rate} | "
                f"历史好评率: {ent.historical_positive_rate} | "
                f"上架天数: {ent.game_age_days}\n"
                f"  玩家原话片段: {quotes[:200]}"
            )
        lines.append(
            f"{i}. {sig.entity_name} (appid={sig.entity_id})\n"
            f"  信号: {sig.signal_type} | severity: {sig.severity} | score: {sig.score}\n"
            f"  raw: {sig.raw_data}\n"
            f"{ent_summary}\n"
        )

    strictness = tuning.judgment.strictness
    criteria_block = _strictness_criteria_block(strictness, len(signals))

    # v5: only v0_ready content_types
    from xhs_agent.domain.games.templates import V0_READY_TEMPLATES

    template_options = " / ".join(
        f"{t.name}（{t.content_type}）" for t in V0_READY_TEMPLATES
    )

    user_message = (
        "你在帮一个叫『真实玩家评论研究所』的小红书账号筛选选题。"
        "账号定位是『理性游戏消费分析员』，用真实评论 + 数据告诉用户值不值得买。\n\n"
        "下面是 Steam 数据自动检测出的候选信号。请判断每条值不值得做一篇笔记，"
        "并推荐内容模板（content_type）。\n\n"
        + "\n".join(lines)
        + "\n\n"
        f"可选模板：{template_options}\n\n"
        "## 重要：signal_type 不直接决定 content_type\n"
        "signal_type 是机器检测的现象（'差评爆发' / '在线人数异常' 等），"
        "你要看 entity 实际数据决定文章方向：\n"
        "- 上架 < 30 天 + 在线高 + 好评率中位（0.5-0.7） → 一般是 new_release_heat（新品爆款，但要看玩家说什么）\n"
        "- 近 7 天好评率明显低于历史 → negative_review_burst（差评爆炸）\n"
        "- 近 7 天好评率明显高于历史 + 老游戏（age > 180d）→ comeback_game（口碑反转）\n"
        "- 评论数低 + 好评率高（> 0.85）→ hidden_gem（小众神作）\n"
        "- player_spike 只是说人多，不代表内容方向；要看评论情绪\n\n"
        + criteria_block
        + "\n\n## 输出\n"
        "调用 submit_judgments 工具。每条信号一个 entry。"
        "reasoning 字段必须说清楚为什么选这个 content_type（看了什么数据）。"
    )

    system = _strictness_system_prompt(strictness)
    return AssembledPrompt(
        system=system,
        user_message=user_message,
        used_meme_ids=[],
        used_exemplar_ids=[],
        template_name="signal_judgment",
    )


# ============================================================
def _external_viewpoints_section(entity) -> str:
    """Build an external community viewpoints section for the prompt, if available."""
    viewpoints = getattr(entity, "key_viewpoints", None) or []
    if not viewpoints:
        return ""
    sources_label = getattr(entity, "_sources_label", "外部来源")
    sentiment_map = {"positive": "✅ 好评方向", "negative": "❌ 差评方向", "neutral": "ℹ 中立观察"}
    lines = []
    for vp in viewpoints[:5]:
        label = sentiment_map.get(vp.sentiment, vp.sentiment)
        line = f"- [{label}] {vp.zh_summary}"
        if vp.en_quote:
            line += f'（原文："{vp.en_quote}"）'
        lines.append(line)
    return (
        f"\n## 外部社区讨论精华（来源：{sources_label}）\n"
        + "\n".join(lines)
        + "\n请在正文中自然引用1-2条（用『外网玩家普遍认为』或『社区讨论显示』引出）\n"
    )


def _language_dist_line(entity) -> str:
    """Return a one-line language distribution note, e.g. '（中文 62%，英文 28%）'."""
    dist: dict = getattr(entity, "review_language_dist", None) or {}
    total = sum(dist.values())
    if not dist or total == 0:
        return ""
    zh = dist.get("schinese", 0) + dist.get("tchinese", 0)
    en = dist.get("english", 0)
    zh_pct = round(zh / total * 100)
    en_pct = round(en / total * 100)
    other_pct = 100 - zh_pct - en_pct
    parts = []
    if zh_pct:
        parts.append(f"中文 {zh_pct}%")
    if en_pct:
        parts.append(f"英文 {en_pct}%")
    if other_pct > 5:
        parts.append(f"其他 {other_pct}%")
    return f"（语言分布：{'，'.join(parts)}）" if parts else ""


def _player_trend_line(entity) -> str:
    """Return a one-line trend summary for the prompt, or empty string."""
    trend_pct = getattr(entity, "player_count_trend_pct", None)
    peak = getattr(entity, "player_count_all_time_peak", None)
    peak_month = getattr(entity, "player_count_peak_month", None)
    if trend_pct is None or peak is None:
        return ""
    if trend_pct <= -20:
        return f"（历史峰值 {peak:,}，{peak_month}，较峰值下降 {abs(int(trend_pct))}%）"
    if trend_pct >= 10:
        return f"（较3个月前上涨 {int(trend_pct)}%，历史峰值 {peak:,}）"
    return f"（历史峰值 {peak:,}，{peak_month}）"


# Strictness modes
# ============================================================


def _strictness_system_prompt(mode: str) -> str:
    if mode == "strict":
        return (
            "你是『真实玩家评论研究所』的严格选题编辑。"
            "只有真正有数据故事 + 消费决策价值的信号才通过。"
        )
    if mode == "lenient":
        return (
            "你是『真实玩家评论研究所』的选题编辑。"
            "从一批候选里挑能写成消费建议笔记的几条，不要把守门员当成职业。"
        )
    return (
        "你是『真实玩家评论研究所』的选题编辑。"
        "挑出真正能给读者消费决策价值的几条，但不要全部拒。"
    )


def _strictness_criteria_block(mode: str, n_signals: int) -> str:
    if mode == "strict":
        return (
            "## 判断标准\n"
            "- 严格挑选。urgent 信号 + 数据反差强 / 玩家原话有钩子的才通过\n"
            "- normal 信号默认 false，除非素材特别突出\n"
            "- 整批 0 通过是允许的"
        )
    if mode == "lenient":
        target = min(max(1, n_signals // 4), 3)
        return (
            "## 判断标准（不要太严）\n"
            "- urgent 信号默认 worth_writing=true\n"
            "- normal 信号：只要 (a) 玩家原话有可引用的钩子，或 "
            "(b) 数据本身有故事感（老游戏复活、巨头掉评、新品爆发、长时差评），就 true\n"
            "- 只有一条信号既无原话也是常驻巨头的常规波动才 false\n"
            f"- **底线：本批至少批准 {target} 条**。"
        )
    return (
        "## 判断标准\n"
        "- urgent 信号默认 true，但若 reasoning 站不住可以 false\n"
        "- normal 信号要求至少有玩家原话或数据故事感其中一项\n"
        "- 不要一刀切全部 false，但也不要无脑全过"
    )


# ============================================================
# Prompt builders
# ============================================================


def _build_system(persona: Persona) -> str:
    """System prompt — persona + v5 hard constraints."""
    # Flatten banned expressions for explicit inclusion
    banned_flat: list[str] = []
    for category, items in persona.banned_expressions.items():
        for w in items:
            banned_flat.append(w)
    banned_block = "、".join(banned_flat) if banned_flat else "(无)"

    return f"""你是『{persona.account_name}』的 AI 写作员。

{persona.text}

## v5 硬性规则
- **标题必须包含《游戏名》**，字数 {persona.title_min_chars}-{persona.title_max_chars}
- 正文目标 {persona.content_target} 字，最多 {persona.paragraphs_max} 段
- hashtag 数量 {persona.hashtag_count}（系统会做最终拼装，你贡献内容相关的 tag 即可）
- 永远调用提供的工具（tool_use）输出结构化结果，不要在 tool 外说话

## 绝对不要使用的词 / 表达
{banned_block}

## 鼓励使用的表达风格
{chr(10).join(f"- {p}" for p in persona.preferred_phrasings[:8])}
""".strip()


def _build_user_message(
    *,
    persona: Persona,
    template: Template,
    signal: SignalResult,
    entity: GameEntity,
    angle_hint: Optional[str],
    exemplar_blocks: list[str],
    theme_summary: Optional["ThemeSummary"] = None,
    playtime_buckets: Optional["PlaytimeBucketResult"] = None,
    price_context: Optional[str] = None,
) -> str:
    quotes = "\n".join(
        f"  - {q}" for q in entity.sample_recent_review_excerpts[:5]
    ) or "  (无可用)"

    angle_section = (
        f"\n## 这次的切入角度\n{angle_hint}\n" if angle_hint else ""
    )

    # User-provided background (manual /gen trigger only) — inject in full
    user_direction = getattr(entity, "_user_direction", None)
    user_direction_section = (
        f"\n## 用户提供的写作背景（重要：请将下述事件/数据自然融入文章）\n"
        f"{user_direction}\n"
        if user_direction else ""
    )

    # Title patterns for this content_type group (best-effort match)
    relevant_patterns: list[str] = []
    for group_name, patterns in persona.title_patterns.items():
        if template.content_type in group_name or (
            template.name == "negative_review_burst" and "差评" in group_name
        ) or (
            template.name == "comeback_game" and "口碑" in group_name
        ) or (
            template.name == "hidden_gem" and "小众" in group_name
        ) or (
            template.name == "discount_worth_checking" and "折扣" in group_name
        ) or (
            template.name == "playtime_contrast" and "反差" in group_name
        ):
            relevant_patterns.extend(patterns)
    if not relevant_patterns:
        for pats in persona.title_patterns.values():
            relevant_patterns.extend(pats)
    title_pattern_lines = "\n".join(f"- {p}" for p in relevant_patterns[:6])

    opening_lines = "\n".join(f"- {o}" for o in persona.recommended_openings[:5])
    closing_lines = "\n".join(f"- {c}" for c in persona.recommended_comment_prompts[:3])

    exemplars_section = ""
    if exemplar_blocks:
        exemplars_section = (
            "\n## 同类爆款参考（语感校准用，不要照抄）\n\n"
            + "\n\n".join(exemplar_blocks)
        )

    # Theme summary — real percentages from Review Miner classification
    theme_section = ""
    if theme_summary and theme_summary.success and theme_summary.themes:
        theme_section = (
            "\n## 评论主题数据（基于 14 类固定分类，来自 Review Miner，请用这些百分比写作）\n"
            + theme_summary.format_for_prompt()
            + "\n"
        )

    # Playtime buckets — shows who's complaining (short-time vs long-time players)
    playtime_section = ""
    if playtime_buckets and playtime_buckets.total > 0:
        playtime_section = (
            "\n## 游玩时长分组（来自 Review Miner，请在分析里引用）\n"
            + playtime_buckets.format_for_prompt()
            + "\n"
        )

    # Price history section (only for 折扣值不值 or when ITAD data is available)
    price_section = ""
    if price_context:
        price_section = f"\n## 价格历史数据\n{price_context}\n"
    elif entity.is_on_special and entity.final_price is not None:
        parts = []
        if entity.discount_pct:
            parts.append(f"当前折扣：{entity.discount_pct}% off，折后价 ${entity.final_price}，原价 ${entity.original_price}")
        if entity.historic_low_price is not None:
            if entity.is_at_historic_low:
                parts.append(f"📉 当前价格接近历史最低（史低 ${entity.historic_low_price}，相差 ≤5%）")
            else:
                parts.append(f"史低记录：${entity.historic_low_price}（当前高出史低 {entity.pct_above_historic_low:.1f}%）")
        if parts:
            price_section = "\n## 价格历史数据（请在正文里引用）\n" + "\n".join(parts) + "\n"

    genres_str = ", ".join(entity.genres) if entity.genres else "(未知)"

    user_message = f"""
## 任务
为下面这个游戏写一篇『真实玩家评论研究所』的小红书笔记。

## content_type
{template.content_type} — {template.tone}

## 切入参考（仅供启发，不是必须涵盖的清单）
- 钩子参考方向：{template.hook}
- 这类内容通常会聊到：{', '.join(template.structure[:4])}
- 备注：{template.notes or '(无)'}
{angle_section}
## 信号背景
游戏：{entity.name} (Steam appid: {entity.appid})
游戏类型: {genres_str}
信号类型：{signal.signal_type}（severity={signal.severity}, score={signal.score}）
关键数据：{signal.raw_data}
当前在线人数：{entity.current_player_count}{_player_trend_line(entity)}
近 7 天评论数：{entity.recent_7d_review_count}{_language_dist_line(entity)}
近 7 天好评率：{entity.recent_7d_positive_rate}
历史好评率：{entity.historical_positive_rate}
上架天数：{entity.game_age_days}
是否新品/热销：is_new_release={entity.is_new_release}, top_seller_rank={entity.top_seller_rank}
Steam 商店页：{entity.store_url}

## 玩家原话片段（请挑 1-2 条引用，每条 ≤ 100 字）
{quotes}
{user_direction_section}{_external_viewpoints_section(entity)}{price_section}{theme_section}{playtime_section}

## 标题方向（必须含《{entity.name}》）
参考思路：
{title_pattern_lines or '(无)'}

## 开头句式池（每篇换一种，不要总用同一种；也可以自己写）
{opening_lines or '(无)'}

## 结尾互动句（选一个或自己写一个类似的）
{closing_lines or '(无)'}
{exemplars_section}

## 写作硬性要求（最重要，认真看）

**1. 数据分析师口吻，不是博主自述。绝对不要自称"我"。**
- ❌ 错误："我翻了最近评论"、"我看了 100 条评论"、"我替你读了"、"我研究了"、"我的判断是"
- ✅ 正确："近期 33 条评论中，约 42% 集中在连接问题"、"数据显示"、"评论区可见"、
  "高频出现的反馈是"、"主题分类后看到"、"按分类统计"
- 例外：开头池里出现"我"的句式可以用，但整篇正文不要再有"我"自称。

**2. 用百分比和频次说话，不用模糊词。**
- ❌ "出现频率相当高"、"很多人提到"、"一些玩家觉得"、"普遍反映"
- ✅ "约 27% 的评论指向 X"、"差评里 60% 集中在 X"、"33 条中 8 条提到 X"
- 上面【评论主题数据】section 给了真实数字，直接引用；不要乱编百分比。

**3. 不要套用大纲式结构。**
- ❌ 错误：用「先说结论 / XX 主要集中在 / 适合谁不适合谁 / 消费建议」当小标题分段
- ✅ 正确：散文段落，要点自然铺开，不要每篇都套同一个骨架。

**4. 不要每篇用同一个开头。**
- "我替你读了《XX》评论…"、"先说结论…" 这种开头只能偶尔用。
- 更推荐：直接抛具体数据（"近 7 天 33 条评论里"）或玩家原话作钩子。
- 标题党一点没关系，但内容要给真数据。

**5. 标题要"重"，开头不要重复标题。**
- 标题已经讲了 "{entity.name} 怎么了"。
- 正文第一句不要重复"近期《{entity.name}》..."这种废话。直接进具体数字。

**6. 消费判断必须给，但不需要单独"消费建议"小标题。**
- 自然带出 A/B/C/D/E 级别中的判断（用文字描述："等首个补丁更稳妥"等）。
- 不必硬塞"A 级"字母。

## 输出
调用 submit_post 工具。
- title：含《{entity.name}》，{persona.title_min_chars}-{persona.title_max_chars} 字
- content：{persona.content_target} 字，散文风，结尾用一句互动句
- hashtags：3-6 个内容相关的 hashtag（不用包含 #Steam游戏 这种通用的——系统会另外加）
""".strip()

    return user_message

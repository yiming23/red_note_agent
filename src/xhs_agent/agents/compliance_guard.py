"""Compliance Guard — validates multi-page post output before Telegram push.

Rule-based (no LLM cost). Checks every page's text content for:
1. Title contains 《game_name》 (block if missing)
2. Hashtag count 5-10 + contains fixed set (block if wrong)
3. Any single review quote exceeds 100 chars (warn)
4. Absolute-language phrases still present after formatter rewrite (auto-fix)
5. Claims like "所有玩家" / "全部玩家" (auto-fix)
6. Developer-accusation phrases (warn)
7. Buy recommendation avoids absolute language (warn)

DESIGN_v5.md §10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from xhs_agent.config import tuning
from xhs_agent.observability.logger import get_logger
from xhs_agent.utils.prohibited_words import rewrite_banned_phrases

if TYPE_CHECKING:
    from xhs_agent.agents.buy_or_wait import BuyRecommendation

log = get_logger(__name__)

# Phrases that should never appear in the final output
_ABSOLUTE_PHRASES: dict[str, str] = {
    "垃圾游戏": "目前首发状态不适合普通玩家原价冲",
    "千万别买": "如果你在意优化和稳定性，建议先观望",
    "必买神作": "如果你喜欢这个类型，值得放进愿望单",
    "闭眼入": "如果你喜欢这个类型，可以考虑",
    "所有玩家都在骂": "最近差评比较集中，主要集中在几个问题上",
    "全部玩家都觉得": "最近差评比较集中，部分玩家反映",
    "全部玩家觉得": "最近差评比较集中，部分玩家反映",
    "年度最烂": "目前首发状态争议较大",
    "谁玩谁后悔": "不建议无脑入",
}

_DEV_ACCUSATION_PATTERNS = [
    r"开发[组团队]跑路",
    r"[开发商厂商]不管了",
    r"弃坑",
]

_SPOILER_PATTERNS = [
    r"最终[BOSS关卡]",
    r"结局是",
    r"真相是",
]


@dataclass
class ComplianceReport:
    blocks: list[str] = field(default_factory=list)    # hard failures — must fix before push
    warnings: list[str] = field(default_factory=list)  # soft issues — logged but don't block
    rewrites: list[tuple[str, str]] = field(default_factory=list)  # (original, replacement)
    title_ok: bool = True
    hashtag_ok: bool = True

    @property
    def passed(self) -> bool:
        return not self.blocks

    def summary_for_telegram(self) -> str:
        if self.passed and not self.warnings:
            return "✅ 合规检查通过"
        parts: list[str] = []
        for b in self.blocks:
            parts.append(f"🚫 {b}")
        for w in self.warnings:
            parts.append(f"⚠️ {w}")
        for orig, repl in self.rewrites:
            parts.append(f"🔄 已替换：「{orig}」→「{repl[:30]}」")
        return "\n".join(parts) if parts else "✅ 合规检查通过"


def check(
    title: str,
    content: str,
    hashtags: list[str],
    game_name: Optional[str] = None,
    buy_rec: Optional["BuyRecommendation"] = None,
    pages: Optional[list[dict]] = None,
) -> tuple[str, str, ComplianceReport]:
    """Run all compliance rules. Returns (clean_title, clean_content, report).

    Mutates title/content to apply safe auto-rewrites. Blocks are not auto-fixed —
    they must be surfaced to the user.
    """
    report = ComplianceReport()

    # ── 1. Title: must contain 《game_name》 ──────────────────
    if game_name:
        bracket = f"《{game_name}》"
        if bracket not in title:
            report.blocks.append(f"标题缺少《{game_name}》")
            report.title_ok = False
    title_len = len(title)
    min_c = tuning.compliance.title.min_chars
    max_c = tuning.compliance.title.max_chars
    if not (min_c <= title_len <= max_c):
        report.blocks.append(f"标题字数 {title_len}，要求 {min_c}-{max_c} 字")
        report.title_ok = False

    # ── 2. Hashtag: 5-10 + fixed set ─────────────────────────
    fixed_required = {"#Steam游戏", "#真实玩家评论", "#游戏值不值得买", "#游戏评论研究所"}
    present = set(hashtags)
    n = len(hashtags)
    min_h = tuning.hashtag.total_min
    max_h = tuning.hashtag.total_max
    if not (min_h <= n <= max_h):
        report.blocks.append(f"Hashtag 数量 {n}，要求 {min_h}-{max_h}")
        report.hashtag_ok = False
    missing_fixed = fixed_required - present
    if missing_fixed:
        report.warnings.append(f"缺少固定 hashtag：{' '.join(missing_fixed)}")

    # ── 3. Review quotes ≤ 100 chars ─────────────────────────
    quote_max = tuning.compliance.review_quote.max_chars_per_quote
    # Heuristic: extract text inside 「」 or "" or ''
    quote_patterns = [r'「([^」]{' + str(quote_max + 1) + r',})」',
                      r'"([^"]{' + str(quote_max + 1) + r',})"']
    for pat in quote_patterns:
        for m in re.finditer(pat, content):
            report.warnings.append(f"引用过长（{len(m.group(1))} 字，上限 {quote_max}）：{m.group(1)[:40]}…")

    # ── 4. Absolute language auto-rewrite ────────────────────
    for phrase, replacement in _ABSOLUTE_PHRASES.items():
        if phrase in content:
            content = content.replace(phrase, replacement)
            report.rewrites.append((phrase, replacement))
            log.debug("compliance_rewrite", phrase=phrase)
        if phrase in title:
            title = title.replace(phrase, replacement)
            report.rewrites.append((phrase, replacement))

    # Also apply tuning.yaml banned_phrases_to_rewrite
    clean_content, extra_rewrites = rewrite_banned_phrases(content)
    if extra_rewrites:
        content = clean_content
        report.rewrites.extend(extra_rewrites)

    # ── 5. Developer-accusation patterns ─────────────────────
    for pat in _DEV_ACCUSATION_PATTERNS:
        if re.search(pat, content):
            report.warnings.append(f"可能含未确认的开发者指控（{pat}）")

    # ── 6. Spoiler patterns ───────────────────────────────────
    for pat in _SPOILER_PATTERNS:
        if re.search(pat, content):
            report.warnings.append(f"可能含剧透内容（{pat}）")

    # ── 7. Buy-rec absolute language ─────────────────────────
    if buy_rec:
        bad_in_rec = [p for p in ("千万别买", "必买", "闭眼入", "垃圾") if p in buy_rec.one_sentence]
        if bad_in_rec:
            report.warnings.append(f"购买建议含绝对化表达：{'、'.join(bad_in_rec)}")

    return title, content, report

"""Review Miner — extract theme stats from a pool of Steam reviews.

V5 S5: Adds DB persistence and 24h cache to avoid redundant Haiku calls.
  - Before calling Haiku, checks `review_theme_stats` for a recent run (<24h).
  - On cache hit: reconstructs ThemeSummary from DB rows, skips LLM.
  - On cache miss: runs Haiku batch classification, persists results to DB.

Cost: ~$0.005 per call (1.5k in / 500 out tokens on Haiku).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from xhs_agent.config import settings, tuning
from xhs_agent.observability.llm_tracker import call_llm
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import LlmPurpose
from xhs_agent.storage.repositories import ReviewThemeRepository

log = get_logger(__name__)


# ────────────────────────────────────────────────────────────
# Output structures
# ────────────────────────────────────────────────────────────


@dataclass
class ThemeStat:
    theme: str               # one of tuning.review_themes (or "其他")
    count: int               # how many reviews fell into this theme
    negative_count: int
    positive_count: int
    share_pct: int           # rounded percent of total_analyzed
    sample_quote: Optional[str] = None  # short quote (≤120 chars) representative of this theme


@dataclass
class ThemeSummary:
    themes: list[ThemeStat]
    total_analyzed: int
    cost_usd: float
    success: bool
    error_message: Optional[str] = None

    def top_negative(self, k: int = 3) -> list[ThemeStat]:
        """Top-k themes ranked by negative_count (most-complained-about)."""
        return sorted(self.themes, key=lambda t: -t.negative_count)[:k]

    def top_positive(self, k: int = 3) -> list[ThemeStat]:
        return sorted(self.themes, key=lambda t: -t.positive_count)[:k]

    def format_for_prompt(self) -> str:
        """One-page summary string suitable for injection into content_agent prompt."""
        if not self.themes:
            return "(无主题分析数据)"
        lines: list[str] = [
            f"基于 {self.total_analyzed} 条近期评论的主题分类："
        ]
        # Sort by total count desc
        sorted_themes = sorted(self.themes, key=lambda t: -t.count)
        for t in sorted_themes[:8]:  # top 8 themes
            tag = "差评向" if t.negative_count > t.positive_count else "好评向"
            if t.negative_count == t.positive_count and t.count > 0:
                tag = "好差评对半"
            quote = f"  代表评论：{t.sample_quote}" if t.sample_quote else ""
            lines.append(
                f"- {t.theme}: 约 {t.share_pct}%（{t.count} 条，{tag}：差评 {t.negative_count} / 好评 {t.positive_count}）"
            )
            if t.sample_quote:
                lines.append(quote)
        return "\n".join(lines)


# ────────────────────────────────────────────────────────────
# Tool schema for Haiku structured output
# ────────────────────────────────────────────────────────────


THEME_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "description": "必须是 taxonomy 里的类目名字之一",
                    },
                    "count": {"type": "integer", "minimum": 0},
                    "negative_count": {"type": "integer", "minimum": 0},
                    "positive_count": {"type": "integer", "minimum": 0},
                    "share_pct": {"type": "integer", "minimum": 0, "maximum": 100},
                    "sample_quote": {
                        "type": "string",
                        "description": "≤80 字代表性引用（直接抄一条评论里的关键句）",
                    },
                },
                "required": ["theme", "count", "negative_count", "positive_count", "share_pct"],
            },
        },
        "total_analyzed": {"type": "integer", "minimum": 1},
    },
    "required": ["themes", "total_analyzed"],
}

TOOL_NAME = "submit_theme_summary"
TOOL_DESC = "Submit per-theme statistics over the input review pool."


# ────────────────────────────────────────────────────────────
# DB cache helpers
# ────────────────────────────────────────────────────────────


def get_cached_themes(appid: str, max_age_hours: int = 24) -> Optional[ThemeSummary]:
    """Return a ThemeSummary rebuilt from DB rows if a recent run exists, else None."""
    try:
        with session_scope() as session:
            repo = ReviewThemeRepository(session)
            rows = repo.get_recent_for_appid(appid, max_age_hours=max_age_hours)
            if not rows:
                return None
            themes = [
                ThemeStat(
                    theme=r.theme,
                    count=r.count,
                    negative_count=r.negative_count,
                    positive_count=r.positive_count,
                    share_pct=r.share_pct,
                    sample_quote=r.sample_quote,
                )
                for r in rows
            ]
            total = rows[0].total_analyzed if rows else 0
            log.info("review_miner_cache_hit", appid=appid, themes=len(themes))
            return ThemeSummary(
                themes=themes,
                total_analyzed=total,
                cost_usd=0.0,
                success=True,
            )
    except Exception as e:
        log.warning("review_miner_cache_read_failed", appid=appid, error=str(e))
        return None


def persist_themes(
    appid: str,
    summary: ThemeSummary,
    pipeline_run_id: Optional[int] = None,
) -> None:
    """Write ThemeSummary rows to DB for future cache hits."""
    if not summary.success or not summary.themes:
        return
    try:
        with session_scope() as session:
            repo = ReviewThemeRepository(session)
            repo.save_summary(
                appid=appid,
                themes=summary.themes,
                total_analyzed=summary.total_analyzed,
                pipeline_run_id=pipeline_run_id,
            )
        log.info("review_miner_persisted", appid=appid, themes=len(summary.themes))
    except Exception as e:
        log.warning("review_miner_persist_failed", appid=appid, error=str(e))


# ────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────


def extract_themes(
    pool: list[dict],
    *,
    appid: Optional[str] = None,
    pipeline_run_id: Optional[int] = None,
    use_cache: bool = True,
) -> Optional[ThemeSummary]:
    """Classify each review into one of the fixed taxonomy themes.

    Args:
        pool: list of {"text": str, "voted_up": bool, "playtime_minutes": int|None}
        appid: if provided, enables 24h DB cache (skip Haiku on cache hit)
        pipeline_run_id: for cost tracking
        use_cache: set False to force a fresh Haiku call even if cache exists

    Returns:
        ThemeSummary or None if pool is empty / call failed badly.
    """
    if not pool:
        return None

    # Check 24h cache before calling Haiku
    if use_cache and appid:
        cached = get_cached_themes(appid)
        if cached is not None:
            return cached

    taxonomy = tuning.review_themes
    taxonomy_block = "、".join(taxonomy)

    review_lines: list[str] = []
    for i, r in enumerate(pool, start=1):
        voted = "好评" if r.get("voted_up") else "差评"
        text = (r.get("text") or "").strip()
        if not text:
            continue
        review_lines.append(f"[{i}] {voted}: {text[:200]}")

    if not review_lines:
        return None

    user_message = f"""你在帮一个游戏评论研究账号给 Steam 评论做主题分类。

## 类目（必须从这些里选；不属于任何类的归入"其他"）
{taxonomy_block}

## 评论列表（{len(review_lines)} 条）
{chr(10).join(review_lines)}

## 任务
1. 给每条评论选 1 个最贴的类目
2. 按类目聚合：count（评论数）/ negative_count / positive_count / share_pct（基于 {len(review_lines)}）
3. 每个类目选 1 条最有代表性的短引用（≤80 字，直接从评论里截一句即可）
4. 不要遗漏 count > 0 的类目；count = 0 的可以省略

调用 submit_theme_summary 工具输出。"""

    system = (
        "你是游戏评论数据分析员。准确把每条评论分到给定 14 类之一，统计后输出占比。"
        "保持客观，不评价游戏好坏，只做分类聚合。"
    )

    result = call_llm(
        purpose=LlmPurpose.REVIEW_MINING,
        model=settings.model_signal_judgment,  # Haiku
        messages=[{"role": "user", "content": user_message}],
        system=system,
        max_tokens=2000,
        temperature=0.2,  # classification needs to be stable
        pipeline_run_id=pipeline_run_id,
        estimated_in_tokens=len(user_message) // 3,
        estimated_out_tokens=600,
        tool_schema=THEME_TOOL_SCHEMA,
        tool_name=TOOL_NAME,
        tool_description=TOOL_DESC,
    )

    if not result.success or not result.text:
        log.warning("review_miner_call_failed", error=result.error_message)
        return ThemeSummary(
            themes=[], total_analyzed=0, cost_usd=result.cost_usd,
            success=False, error_message=result.error_message,
        )

    parsed = _parse_response(result.text, valid_themes=set(taxonomy) | {"其他"})
    if parsed is None:
        return ThemeSummary(
            themes=[], total_analyzed=0, cost_usd=result.cost_usd,
            success=False, error_message="parse_failed",
        )

    parsed.cost_usd = result.cost_usd
    parsed.success = True
    log.info(
        "review_miner_done",
        analyzed=parsed.total_analyzed,
        themes=len(parsed.themes),
        cost_usd=round(parsed.cost_usd, 4),
    )

    # Persist to DB so next pipeline run within 24h can skip the LLM call
    if appid:
        persist_themes(appid, parsed, pipeline_run_id=pipeline_run_id)

    return parsed


# ────────────────────────────────────────────────────────────
# Parser
# ────────────────────────────────────────────────────────────


def _parse_response(text: str, valid_themes: set[str]) -> Optional[ThemeSummary]:
    cleaned = _strip_code_fence(text).strip()
    if not cleaned:
        return None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.warning("review_miner_parse_failed", error=str(e), preview=cleaned[:200])
        return None

    if not isinstance(data, dict):
        return None
    themes_raw = data.get("themes") or []
    total = int(data.get("total_analyzed", 0))

    themes: list[ThemeStat] = []
    for t in themes_raw:
        if not isinstance(t, dict):
            continue
        name = str(t.get("theme", "")).strip() or "其他"
        if name not in valid_themes:
            # Force unrecognized into "其他" bucket
            name = "其他"
        themes.append(ThemeStat(
            theme=name,
            count=int(t.get("count", 0)),
            negative_count=int(t.get("negative_count", 0)),
            positive_count=int(t.get("positive_count", 0)),
            share_pct=int(t.get("share_pct", 0)),
            sample_quote=(t.get("sample_quote") or "").strip()[:150] or None,
        ))

    return ThemeSummary(
        themes=themes,
        total_analyzed=total,
        cost_usd=0.0,
        success=True,
    )


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json|JSON)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    return m.group(1) if m else text

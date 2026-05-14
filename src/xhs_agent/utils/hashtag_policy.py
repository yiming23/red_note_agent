"""Hashtag policy — assembles the final 5-10 hashtags per the v5 rules.

DESIGN_v5.md § 2.

Composition:
- 3-4 fixed tags from tuning.hashtag.fixed (always included)
- 2-3 content_type-specific tags from tuning.hashtag.by_content_type
- 1 game-name tag derived from entity.name (e.g. "《星空》" → "#星空")
- 0-2 genre tags translated from entity.genres via tuning.hashtag.genre_translations
- LLM-generated extras are accepted but optional; the policy enforces the floor/ceiling.

Public API:
    build_hashtags(content_type, game_name, genres, llm_suggestions=None) -> list[str]
    validate_hashtags(tags) -> ValidationResult
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from xhs_agent.config import tuning
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

# Pattern to strip leading # plus normalize spacing
_HASH_PREFIX_RE = re.compile(r"^#+\s*")


@dataclass
class HashtagResult:
    tags: list[str]
    sources: dict[str, list[str]]  # debug: which group contributed what


def build_hashtags(
    content_type: str,
    game_name: str,
    genres: list[str] | None = None,
    llm_suggestions: list[str] | None = None,
) -> HashtagResult:
    """Build a deduped, policy-compliant hashtag list.

    Greedy approach: take all fixed tags + all content_type tags + game-name tag
    + translated genres, then top up with LLM suggestions until we hit total_max.
    If still below total_min, log a warning (rare; means content_type unknown).
    """
    policy = tuning.hashtag

    seen: set[str] = set()
    sources: dict[str, list[str]] = {
        "fixed": [],
        "content_type": [],
        "game_name": [],
        "genres": [],
        "llm_suggestions": [],
    }

    def _add(tag: str, source: str) -> bool:
        normalized = _normalize(tag)
        if not normalized or normalized in seen:
            return False
        if len(seen) >= policy.total_max:
            return False
        seen.add(normalized)
        sources[source].append(normalized)
        return True

    # 1. fixed
    for t in policy.fixed:
        _add(t, "fixed")

    # 2. content type
    ct_tags = policy.by_content_type.get(content_type, [])
    if not ct_tags:
        log.warning(
            "hashtag_unknown_content_type",
            content_type=content_type,
            known=list(policy.by_content_type.keys()),
        )
    for t in ct_tags:
        _add(t, "content_type")

    # 3. game name
    game_tag = _game_name_to_tag(game_name)
    if game_tag:
        _add(game_tag, "game_name")

    # 4. genres
    for g in genres or []:
        zh = policy.genre_translations.get(g)
        if zh:
            _add(zh, "genres")

    # 5. LLM suggestions (top up; useful if LLM picked something specific to this game)
    for t in llm_suggestions or []:
        _add(t, "llm_suggestions")

    final = [
        *sources["fixed"],
        *sources["content_type"],
        *sources["game_name"],
        *sources["genres"],
        *sources["llm_suggestions"],
    ]

    if len(final) < policy.total_min:
        log.warning(
            "hashtag_below_min",
            count=len(final),
            min=policy.total_min,
            content_type=content_type,
        )

    return HashtagResult(tags=final, sources=sources)


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str]
    count: int


def validate_hashtags(tags: list[str]) -> ValidationResult:
    """Check that a hashtag list meets v5 policy (count + format)."""
    policy = tuning.hashtag
    issues: list[str] = []
    count = len(tags)

    if count < policy.total_min:
        issues.append(f"hashtag 偏少：{count} 个（下限 {policy.total_min}）")
    if count > policy.total_max:
        issues.append(f"hashtag 偏多：{count} 个（上限 {policy.total_max}）")

    for t in tags:
        if not t.startswith("#"):
            issues.append(f"标签 '{t}' 缺少 # 前缀")
        if " " in t or "\t" in t:
            issues.append(f"标签 '{t}' 含空白字符")

    # Check fixed tags are included (warning, not error — LLM may rewrite)
    fixed_present = sum(1 for t in policy.fixed if t in tags)
    if fixed_present < max(1, len(policy.fixed) - 1):
        issues.append(f"固定 hashtag 缺失（应至少含 {len(policy.fixed) - 1} 个）")

    return ValidationResult(ok=not issues, issues=issues, count=count)


# ============================================================
# Helpers
# ============================================================


def _normalize(tag: str) -> str:
    """Force '#xxx' format, strip whitespace, drop multiple #."""
    if not tag:
        return ""
    t = tag.strip()
    t = _HASH_PREFIX_RE.sub("", t)
    t = re.sub(r"\s+", "", t)
    if not t:
        return ""
    return "#" + t


def _game_name_to_tag(name: str) -> str:
    """Strip 《》 brackets and punctuation from a game name to make a hashtag.

    Examples:
        "《星空》"       → "#星空"
        "Counter-Strike 2" → "#CounterStrike2"
        "Baldur's Gate 3"  → "#BaldursGate3"
    """
    if not name:
        return ""
    cleaned = re.sub(r"[《》\"'’“”\s\-:：，,()（）]+", "", name)
    if not cleaned:
        return ""
    return "#" + cleaned

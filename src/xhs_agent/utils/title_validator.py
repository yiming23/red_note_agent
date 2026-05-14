"""Title validator — enforces v5 title rules.

DESIGN_v5.md § 2: title must contain 《game_name》 + 16-30 chars + 数据感 / 冲突点.

This module checks the deterministic parts (brackets, name match, length).
Semantic quality (是否有数据感) is a Compliance Guard concern (S6).

Public API:
    validate_title(title, game_name) -> ValidationResult
    auto_fix_title(title, game_name) -> str   # fallback: prepend 《name》 if missing
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from xhs_agent.config import tuning
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

# Match Chinese book-title brackets 《...》
_BRACKETS_RE = re.compile(r"《([^《》]+)》")


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str]
    char_count: int
    detected_game_name: str | None  # what's between 《》, if anything


def validate_title(title: str, game_name: str) -> ValidationResult:
    """Check a generated title against v5 rules."""
    rules = tuning.compliance.title
    issues: list[str] = []
    char_count = _count_chars(title)

    # 1. brackets present
    match = _BRACKETS_RE.search(title)
    detected = match.group(1) if match else None

    if rules.must_contain_brackets and not match:
        issues.append("标题缺少《》括号（必须把游戏名包在《》里）")

    # 2. brackets content matches game_name (allow partial / fuzzy)
    if detected and game_name:
        if not _names_match(detected, game_name):
            issues.append(
                f"《》里是 '{detected}'，但实际游戏名是 '{game_name}'"
            )

    # 3. length
    if char_count < rules.min_chars:
        issues.append(f"标题偏短：{char_count} 字（下限 {rules.min_chars}）")
    if char_count > rules.max_chars:
        issues.append(f"标题偏长：{char_count} 字（上限 {rules.max_chars}）")

    return ValidationResult(
        ok=not issues,
        issues=issues,
        char_count=char_count,
        detected_game_name=detected,
    )


def auto_fix_title(title: str, game_name: str) -> str:
    """If title is missing 《game_name》, try to inject it.

    This is a last-resort fallback — content_agent should produce correct titles
    on the first pass. If we get here, it means the LLM didn't follow instructions
    and we'd rather ship a slightly awkward fix than a broken title.

    Strategy:
    - If title has no 《》 at all → prepend "《{game_name}》"
    - If title has 《X》 but X != game_name → swap to 《game_name》
    - Don't truncate even if over length (let validation flag it for human review)
    """
    if not title or not game_name:
        return title

    match = _BRACKETS_RE.search(title)
    if match:
        if _names_match(match.group(1), game_name):
            return title
        # Replace the existing bracket content
        return title[: match.start()] + f"《{game_name}》" + title[match.end():]

    # No brackets at all — prepend
    # If title already starts with game_name unbracketed, just wrap it
    if title.startswith(game_name):
        return f"《{game_name}》" + title[len(game_name):].lstrip("：:，, ")
    return f"《{game_name}》{title}"


# ============================================================
# Helpers
# ============================================================


def _count_chars(s: str) -> int:
    """Count characters; Chinese chars count as 1 (same as ASCII for our purposes).

    We do NOT use len(s) directly to give us flexibility to change rules later
    (e.g. count Chinese as 2 to match social-platform conventions).
    """
    return len(s)


def _names_match(detected: str, expected: str) -> bool:
    """Loose match for game names — handles minor punctuation/spacing differences."""
    def clean(s: str) -> str:
        return re.sub(r"[\s\-:：®™©（）()【】\[\]·\.]+", "", s).lower()

    return clean(detected) == clean(expected) or clean(expected) in clean(detected) or clean(detected) in clean(expected)

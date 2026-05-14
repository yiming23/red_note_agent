"""V5 compliance helpers — banned-phrase rewrite + illegal-content detection.

Two responsibilities live here:

1. **Rewrite** — for stylistic / compliance issues (e.g. "垃圾游戏" → "现在不建议
   原价冲"). Replacement table comes from `tuning.compliance.banned_phrases_to_rewrite`,
   so you can edit it without touching code.

2. **Block** — for content that should hard-stop publishing regardless of context
   (illegal content like adult/gambling/cheat/piracy). Kept as a small hardcoded
   list since these don't need tuning.

v4 had `find_violations` mixing both; v5 splits them.

Public API:
    rewrite_banned_phrases(text)  -> (rewritten_text, applied_substitutions)
    find_illegal(text)            -> list of (word, category)
    has_blocking_violation(text)  -> bool   (true if illegal content found)
    find_violations(text)         -> kept as compat alias for old tests; returns
                                     illegal hits with severity="block"
"""

from __future__ import annotations

from xhs_agent.config import tuning
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)


# ============================================================
# Illegal / TOS — hardcoded, always block. No rewrite makes sense.
# ============================================================

ILLEGAL_WORDS: list[tuple[str, str]] = [
    ("色情", "adult"),
    ("赌博", "gambling"),
    ("六合彩", "gambling"),
    ("外挂下载", "cheat"),
    ("免费破解", "piracy"),
    ("破解版下载", "piracy"),
    ("steam白嫖", "piracy"),
]


# ============================================================
# Rewrite (stylistic / compliance) — from tuning.yaml
# ============================================================


def rewrite_banned_phrases(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Apply all configured banned-phrase substitutions to text.

    Returns:
        (new_text, applied)
        where applied is a list of (banned_phrase, replacement) tuples that
        actually fired (useful for telegram warning display).

    Substitution is case-sensitive (Chinese text doesn't have case) and operates
    on substring matches. Replacements are applied in order of the dict, so if
    one replacement creates another banned phrase you'd want to order carefully —
    in practice these are independent.
    """
    table = tuning.compliance.banned_phrases_to_rewrite
    if not table or not text:
        return text, []

    new_text = text
    applied: list[tuple[str, str]] = []
    for banned, replacement in table.items():
        if banned in new_text:
            new_text = new_text.replace(banned, replacement)
            applied.append((banned, replacement))

    if applied:
        log.info(
            "compliance_phrases_rewritten",
            count=len(applied),
            banned=[b for b, _ in applied],
        )
    return new_text, applied


# ============================================================
# Block (illegal)
# ============================================================


def find_illegal(text: str) -> list[tuple[str, str]]:
    """Return list of (matched_word, category) for illegal content in text."""
    seen: set[str] = set()
    hits: list[tuple[str, str]] = []
    lower = text.lower()
    for word, category in ILLEGAL_WORDS:
        if word.lower() in lower:
            if word not in seen:
                seen.add(word)
                hits.append((word, category))
    return hits


def has_blocking_violation(text: str) -> bool:
    return bool(find_illegal(text))


# ============================================================
# Compat shim — keeps old `find_violations` API alive so existing
# imports + tests don't all blow up at once. Returns (word, category, severity)
# triples, severity always "block" since v5 rewrites the soft cases.
# ============================================================


def find_violations(text: str) -> list[tuple[str, str, str]]:
    """Legacy alias. Prefer find_illegal() in new code."""
    return [(w, cat, "block") for w, cat in find_illegal(text)]

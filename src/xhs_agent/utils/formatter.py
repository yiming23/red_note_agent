"""V5 post formatter — integrates banned-phrase rewrite + title + hashtag policy.

Flow (left-to-right):
  raw_title ─┐
  raw_content │── 1. clean (markdown strip, quote strip, paragraph split)
  raw_hashtags ┘
              │── 2. banned-phrase rewrite (title + content)
              │── 3. title validate + auto-fix《game_name》
              │── 4. hashtag policy assemble (fixed + content_type + game + genres + LLM extras)
              │── 5. illegal-content scan (last-line defense)
              └── FormattedPost { full_text, violations, warnings, rewrites_applied, ... }

If `content_type` / `game_name` / `genres` aren't passed (e.g. in unit tests without
full context), v5 enforcement is downgraded gracefully:
  - title validator skips《》check
  - hashtag policy still runs but with empty content_type slot (only fixed + LLM extras)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from xhs_agent.observability.logger import get_logger
from xhs_agent.prompt.assembler import load_persona
from xhs_agent.utils.hashtag_policy import build_hashtags, validate_hashtags
from xhs_agent.utils.prohibited_words import find_illegal, rewrite_banned_phrases
from xhs_agent.utils.title_validator import auto_fix_title, validate_title

log = get_logger(__name__)


@dataclass
class FormattedPost:
    title: str
    content: str
    hashtags: list[str]
    full_text: str

    # v5 enforcement output
    title_issues: list[str] = field(default_factory=list)
    hashtag_issues: list[str] = field(default_factory=list)
    rewrites_applied: list[tuple[str, str]] = field(default_factory=list)
    illegal_hits: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_blocking_violation(self) -> bool:
        return bool(self.illegal_hits)

    @property
    def violations(self) -> list[tuple[str, str, str]]:
        """Compat alias for v4 callers expecting (word, category, severity) tuples."""
        return [(w, cat, "block") for w, cat in self.illegal_hits]


def format_post(
    title: str,
    content: str,
    hashtags: list[str],
    *,
    content_type: Optional[str] = None,
    game_name: Optional[str] = None,
    genres: Optional[list[str]] = None,
) -> FormattedPost:
    """Clean, rewrite, validate, and assemble a generated post for publication.

    Args:
        title / content / hashtags: raw output from content_agent or rewrite_agent
        content_type: Chinese name (差评爆炸 / 口碑反转 / ...) for hashtag policy
        game_name: needed for title 《》 validation + name hashtag
        genres: Steam genre English names for hashtag translation
    """
    persona = load_persona()
    warnings: list[str] = []

    # 1. Clean
    title_clean = _clean_title(title)
    content_clean = _clean_content(content)

    # 2. Rewrite banned phrases (operate on cleaned text)
    title_clean, title_rewrites = rewrite_banned_phrases(title_clean)
    content_clean, content_rewrites = rewrite_banned_phrases(content_clean)
    rewrites_applied = title_rewrites + content_rewrites

    # 3. Title validation + auto-fix
    #
    # auto_fix_title is idempotent: if 《name》 is already correct it returns the
    # input unchanged; if 《》 is missing it prepends them; if the wrong name is
    # in the brackets it swaps. So we always call it when we have a game_name,
    # then run validation to capture any remaining issues (length, etc.).
    title_issues: list[str] = []
    if game_name:
        fixed = auto_fix_title(title_clean, game_name)
        if fixed != title_clean:
            log.info("title_auto_fixed", before=title_clean[:40], after=fixed[:40])
            title_clean = fixed
        title_issues = validate_title(title_clean, game_name).issues
    else:
        # No game context — just length check
        char_count = len(title_clean)
        if char_count < persona.title_min_chars:
            title_issues.append(f"标题偏短：{char_count}/{persona.title_min_chars}")
        if char_count > persona.title_max_chars:
            title_issues.append(f"标题偏长：{char_count}/{persona.title_max_chars}")

    # 4. Paragraph + length sanity on content
    paragraphs = _split_paragraphs(content_clean, max_paragraphs=persona.paragraphs_max)
    if len(paragraphs) > persona.paragraphs_max:
        warnings.append(f"段落数 {len(paragraphs)} > 上限 {persona.paragraphs_max}")
    content_clean = "\n\n".join(paragraphs)

    char_count = len(content_clean)
    low, high = _parse_range(persona.content_target)
    if low and char_count < low * 0.6:
        warnings.append(f"正文偏短：{char_count} 字（目标 {persona.content_target}）")
    if high and char_count > high * 1.5:
        warnings.append(f"正文偏长：{char_count} 字（目标 {persona.content_target}）")

    # 5. Hashtag assembly via policy
    llm_hashtags = _clean_hashtags(hashtags)
    if content_type and game_name is not None:
        hr = build_hashtags(
            content_type=content_type,
            game_name=game_name,
            genres=genres or [],
            llm_suggestions=llm_hashtags,
        )
        final_hashtags = hr.tags
    else:
        # No content_type → just normalize whatever LLM gave us
        final_hashtags = llm_hashtags

    hashtag_validation = validate_hashtags(final_hashtags)
    hashtag_issues = hashtag_validation.issues

    # 6. Illegal-content scan
    full_text = _assemble_full_text(title_clean, content_clean.split("\n\n"), final_hashtags)
    illegal_hits = find_illegal(full_text)

    if rewrites_applied:
        log.info(
            "formatter_rewrites_applied",
            count=len(rewrites_applied),
            phrases=[b for b, _ in rewrites_applied],
        )
    if illegal_hits:
        log.warning(
            "formatter_illegal_content",
            count=len(illegal_hits),
            words=[w for w, _ in illegal_hits],
        )

    return FormattedPost(
        title=title_clean,
        content=content_clean,
        hashtags=final_hashtags,
        full_text=full_text,
        title_issues=title_issues,
        hashtag_issues=hashtag_issues,
        rewrites_applied=rewrites_applied,
        illegal_hits=illegal_hits,
        warnings=warnings,
    )


# ============================================================
# Cleaning helpers
# ============================================================


_QUOTE_TRIM_RE = re.compile(r"^[\"'“”‘’\s]+|[\"'“”‘’\s]+$")
_MD_BOLD_RE = re.compile(r"\*{1,2}([^*]+)\*{1,2}")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def _clean_title(title: str) -> str:
    title = (title or "").strip()
    title = _QUOTE_TRIM_RE.sub("", title)
    title = _MD_BOLD_RE.sub(r"\1", title)
    title = _MD_HEADING_RE.sub("", title)
    return title.strip()


def _clean_content(content: str) -> str:
    content = (content or "").strip()
    content = _MD_HEADING_RE.sub("", content)
    content = _MD_BOLD_RE.sub(r"\1", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def _split_paragraphs(content: str, max_paragraphs: int) -> list[str]:
    raw = [p.strip() for p in content.split("\n\n") if p.strip()]
    if len(raw) <= max_paragraphs:
        return raw
    head = raw[: max_paragraphs - 1]
    tail = "  ".join(raw[max_paragraphs - 1 :])
    return head + [tail]


def _clean_hashtags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in tags or []:
        if not raw:
            continue
        t = str(raw).strip()
        t = _QUOTE_TRIM_RE.sub("", t)
        if not t.startswith("#"):
            t = "#" + t
        t = re.sub(r"\s+", "", t)
        if t in ("#", ""):
            continue
        if t in seen:
            continue
        seen.add(t)
        cleaned.append(t)
    return cleaned


def _assemble_full_text(title: str, paragraphs: list[str], hashtags: list[str]) -> str:
    body = "\n\n".join(p for p in paragraphs if p.strip())
    tags_line = " ".join(hashtags) if hashtags else ""
    parts = [title.strip(), body.strip()]
    if tags_line:
        parts.append(tags_line)
    return "\n\n".join(p for p in parts if p)


def _parse_range(s: str) -> tuple[int | None, int | None]:
    if not s:
        return None, None
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^\s*(\d+)\s*$", s)
    if m:
        n = int(m.group(1))
        return n, n
    return None, None

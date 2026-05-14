"""content_agent — Sonnet writes the actual title/content/hashtags.

Input: SignalJudgment (from signal_agent) + GameEntity + Domain (for templates)
Output: GeneratedContent dataclass

Cost target: ~$0.015 per call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from xhs_agent.agents.signal_agent import SignalJudgment
from xhs_agent.config import settings
from xhs_agent.domain.base import Domain, Template
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.llm_tracker import call_llm
from xhs_agent.observability.logger import get_logger
from xhs_agent.prompt.assembler import (
    AssembledPrompt,
    assemble_content_prompt,
    assemble_rewrite_prompt,
)
from xhs_agent.storage.models import LlmPurpose

if TYPE_CHECKING:
    from xhs_agent.agents.review_miner import ThemeSummary
    from xhs_agent.processors.playtime_buckets import PlaytimeBucketResult

log = get_logger(__name__)


# JSON Schema for the post tool — used to force structured output via Anthropic tool use.
# Eliminates JSON-in-text parsing failures (e.g. unescaped Chinese quotes inside content).
POST_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "小红书帖子标题，≤20 字。",
            "maxLength": 30,  # a bit of slack for emoji
        },
        "content": {
            "type": "string",
            "description": "正文，250-400 字，可多段（用 \\n\\n 分隔）。引用玩家原话用中文引号「」即可，不需要转义。",
        },
        "hashtags": {
            "type": "array",
            "description": "3-5 个 hashtag，每个以 # 开头。",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 8,
        },
    },
    "required": ["title", "content", "hashtags"],
}

POST_TOOL_NAME = "submit_post"
POST_TOOL_DESC = "Submit the final 小红书 post (title + content + hashtags)."


@dataclass
class GeneratedContent:
    """Output of content_agent (or rewrite_agent)."""

    title: str
    content: str
    hashtags: list[str]
    template_name: str
    used_meme_ids: list[int]
    used_exemplar_ids: list[int]
    cost_usd: float
    success: bool
    raw_text: str  # for debug
    error_message: Optional[str] = None


def generate_content(
    judgment: SignalJudgment,
    entity: GameEntity,
    domain: Domain,
    theme_summary: Optional["ThemeSummary"] = None,
    playtime_buckets: Optional["PlaytimeBucketResult"] = None,
    pipeline_run_id: Optional[int] = None,
) -> GeneratedContent:
    """Generate a 小红书 post from a vetted SignalJudgment.

    Args:
        theme_summary: optional Review Miner output to ground the post in real
            percentages. If absent, content_agent falls back to qualitative
            description of the raw review excerpts.
    """
    template_name = judgment.template
    template = _resolve_template(domain, template_name, judgment.signal.signal_type)
    if template is None:
        raise ValueError(
            f"No template available for signal_type={judgment.signal.signal_type}"
        )

    prompt = assemble_content_prompt(
        template=template,
        signal=judgment.signal,
        entity=entity,
        angle_hint=judgment.angle,
        theme_summary=theme_summary,
        playtime_buckets=playtime_buckets,
    )

    return _call_and_parse(
        prompt=prompt,
        purpose=LlmPurpose.COPYWRITING,
        model=settings.model_copywriting,
        pipeline_run_id=pipeline_run_id,
        template_name=template.name,
    )


def _resolve_template(
    domain: Domain, requested_name: Optional[str], signal_type: str
) -> Optional[Template]:
    """Pick a template by name first; fall back to signal-type matching."""
    if requested_name:
        for t in domain.templates:
            if t.name == requested_name:
                return t
        log.warning(
            "content_agent_template_name_unknown",
            requested=requested_name,
            signal_type=signal_type,
        )
    return domain.template_for_signal(signal_type)


def _call_and_parse(
    *,
    prompt: AssembledPrompt,
    purpose: LlmPurpose,
    model: str,
    pipeline_run_id: Optional[int],
    template_name: str,
) -> GeneratedContent:
    estimated_in = len(prompt.user_message) // 3
    estimated_out = 800  # title + content (~300字) + hashtags

    result = call_llm(
        purpose=purpose,
        model=model,
        messages=[{"role": "user", "content": prompt.user_message}],
        system=prompt.system,
        max_tokens=1500,
        temperature=0.85,  # creative, but not chaotic
        pipeline_run_id=pipeline_run_id,
        estimated_in_tokens=estimated_in,
        estimated_out_tokens=estimated_out,
        tool_schema=POST_TOOL_SCHEMA,
        tool_name=POST_TOOL_NAME,
        tool_description=POST_TOOL_DESC,
    )

    title, content, hashtags, error = _parse_response(result.text)

    success = bool(title and content and result.success and not error)

    log.info(
        "content_agent_done",
        purpose=purpose.value,
        success=success,
        title_preview=(title or "")[:30],
        cost_usd=round(result.cost_usd, 4),
    )

    return GeneratedContent(
        title=title or "",
        content=content or "",
        hashtags=hashtags or [],
        template_name=template_name,
        used_meme_ids=prompt.used_meme_ids,
        used_exemplar_ids=prompt.used_exemplar_ids,
        cost_usd=result.cost_usd,
        success=success,
        raw_text=result.text,
        error_message=error or result.error_message,
    )


def _parse_response(text: str) -> tuple[Optional[str], Optional[str], Optional[list[str]], Optional[str]]:
    """Parse the strict-JSON output from the model.

    Returns (title, content, hashtags, error_message). Empty strings for
    missing fields, but error_message non-None on parse failure.
    """
    cleaned = _strip_code_fence(text).strip()
    if not cleaned:
        return None, None, None, "empty_response"

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.warning("content_agent_parse_failed", error=str(e), preview=cleaned[:200])
        return None, None, None, f"json_decode_error: {e}"

    if not isinstance(data, dict):
        return None, None, None, "not_a_json_object"

    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    hashtags_raw = data.get("hashtags") or []

    hashtags: list[str] = []
    if isinstance(hashtags_raw, list):
        for tag in hashtags_raw:
            if not tag:
                continue
            tag = str(tag).strip()
            if not tag.startswith("#"):
                tag = "#" + tag
            hashtags.append(tag)

    return title, content, hashtags, None


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    fence_re = re.compile(r"^```(?:json|JSON)?\s*\n(.*)\n```\s*$", re.DOTALL)
    m = fence_re.match(text)
    if m:
        return m.group(1)
    return text


# ============================================================
# Public helper used by rewrite_agent — exposed so we don't duplicate parsing
# ============================================================


def call_with_assembled_prompt(
    prompt: AssembledPrompt,
    purpose: LlmPurpose,
    model: str,
    template_name: str,
    pipeline_run_id: Optional[int] = None,
) -> GeneratedContent:
    """Hook for rewrite_agent: same generate flow but caller controls the prompt."""
    return _call_and_parse(
        prompt=prompt,
        purpose=purpose,
        model=model,
        pipeline_run_id=pipeline_run_id,
        template_name=template_name,
    )

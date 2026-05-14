"""rewrite_agent — re-runs content_agent with user free-text feedback.

Triggered by Telegram bot when Yiming replies to a pushed candidate with text
(not a button). Takes the original GeneratedContent + the feedback string and
produces a new GeneratedContent.
"""

from __future__ import annotations

from typing import Optional

from xhs_agent.agents.content_agent import (
    GeneratedContent,
    call_with_assembled_prompt,
)
from xhs_agent.config import settings
from xhs_agent.domain.base import Domain, Template
from xhs_agent.observability.logger import get_logger
from xhs_agent.prompt.assembler import assemble_rewrite_prompt
from xhs_agent.storage.models import LlmPurpose

log = get_logger(__name__)


def rewrite(
    *,
    original_title: str,
    original_content: str,
    original_hashtags: list[str],
    feedback: str,
    template_name: Optional[str] = None,
    domain: Optional[Domain] = None,
    game_name: Optional[str] = None,
    pipeline_run_id: Optional[int] = None,
) -> GeneratedContent:
    """Apply user feedback to the original post and return a rewritten version."""
    template: Optional[Template] = None
    if domain and template_name:
        for t in domain.templates:
            if t.name == template_name:
                template = t
                break

    prompt = assemble_rewrite_prompt(
        original_title=original_title,
        original_content=original_content,
        original_hashtags=original_hashtags,
        feedback=feedback,
        template=template,
        game_name=game_name,
    )

    log.info(
        "rewrite_agent_call",
        template=template_name,
        feedback_preview=feedback[:80],
    )

    return call_with_assembled_prompt(
        prompt=prompt,
        purpose=LlmPurpose.REWRITE,
        model=settings.model_rewrite,
        template_name=template_name or "rewrite",
        pipeline_run_id=pipeline_run_id,
    )

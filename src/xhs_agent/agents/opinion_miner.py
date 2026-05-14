"""Opinion Miner — Haiku agent that distills external opinions into structured viewpoints.

Input:  list[ExternalOpinion] (from DuckDuckGo, Reddit, or other sources)
Output: list[KeyViewpoint] — 3-5 key community viewpoints with sentiment

Cost target: ~$0.002 per call (Haiku, ~1k in / 300 out tokens).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from xhs_agent.config import settings
from xhs_agent.observability.llm_tracker import call_llm
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.models import LlmPurpose

log = get_logger(__name__)

OPINION_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "viewpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentiment": {
                        "type": "string",
                        "description": "positive / negative / neutral",
                    },
                    "zh_summary": {
                        "type": "string",
                        "description": "≤30字的中文总结，说明这个观点的核心是什么",
                    },
                    "en_quote": {
                        "type": "string",
                        "description": "≤80 chars, representative English quote from the original text",
                    },
                    "upvotes": {
                        "type": "integer",
                        "description": "Score or rank proxy for this viewpoint",
                    },
                },
                "required": ["sentiment", "zh_summary"],
            },
        }
    },
    "required": ["viewpoints"],
}

OPINION_TOOL_NAME = "submit_viewpoints"
OPINION_TOOL_DESC = "Submit extracted key viewpoints from community discussions."


@dataclass
class KeyViewpoint:
    sentiment: str          # positive / negative / neutral
    zh_summary: str         # ≤30 chars Chinese summary
    en_quote: Optional[str] = None   # original English quote
    upvotes: int = 0


def mine_opinions(
    opinions: list,
    game_name: str,
    sources_label: str = "外部来源",
    pipeline_run_id: Optional[int] = None,
) -> list[KeyViewpoint]:
    """Distill external opinions into 3-5 structured viewpoints. Returns [] on failure."""
    if not opinions:
        return []

    # Build prompt from top opinions (capped to control token cost)
    top_opinions = sorted(opinions, key=lambda o: -o.score)[:12]
    opinions_text = "\n\n".join(
        f"[{o.source.upper()}, score={o.score}]\n{o.text}"
        for o in top_opinions
    )

    system = (
        "You are a community sentiment analyst. Given search results and community discussions "
        "about a game, extract 3-5 distinct key viewpoints that represent common opinions or "
        "notable perspectives. Focus on viewpoints with higher scores. "
        "Each viewpoint must be meaningful and not duplicate others. "
        "Provide a concise Chinese summary (≤30 chars) and an English quote (≤80 chars) for each. "
        "Sentiment: positive=玩家喜欢的方面, negative=玩家批评的方面, neutral=中立观察."
    )

    user_message = (
        f"Game: {game_name}\n"
        f"Sources: {sources_label}\n\n"
        f"Community content (sorted by score):\n\n{opinions_text}"
    )

    estimated_in = (len(system) + len(user_message)) // 3
    estimated_out = 300

    result = call_llm(
        purpose=LlmPurpose.OPINION_MINING,
        model=settings.model_signal_judgment,  # reuse haiku
        messages=[{"role": "user", "content": user_message}],
        system=system,
        max_tokens=500,
        temperature=0.2,
        pipeline_run_id=pipeline_run_id,
        estimated_in_tokens=estimated_in,
        estimated_out_tokens=estimated_out,
        tool_schema=OPINION_TOOL_SCHEMA,
        tool_name=OPINION_TOOL_NAME,
        tool_description=OPINION_TOOL_DESC,
    )

    viewpoints = _parse(result.text)
    log.info(
        "opinion_miner_done",
        game=game_name,
        sources=sources_label,
        viewpoints=len(viewpoints),
        cost_usd=round(result.cost_usd, 4),
    )
    return viewpoints


def _parse(text: str) -> list[KeyViewpoint]:
    cleaned = text.strip()
    # Strip markdown fences
    m = re.match(r"^```(?:json|JSON)?\s*\n(.*)\n```\s*$", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)

    try:
        data = json.loads(cleaned)
        items = data.get("viewpoints", []) if isinstance(data, dict) else []
        results = []
        for item in items:
            sentiment = item.get("sentiment", "neutral")
            zh = (item.get("zh_summary") or "").strip()
            if not zh:
                continue
            results.append(KeyViewpoint(
                sentiment=sentiment,
                zh_summary=zh[:30],
                en_quote=(item.get("en_quote") or "")[:80] or None,
                upvotes=int(item.get("upvotes", 0) or 0),
            ))
        return results
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("opinion_miner_parse_failed", error=str(exc), preview=cleaned[:200])
        return []

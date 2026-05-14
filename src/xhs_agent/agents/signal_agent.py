"""signal_agent — Haiku judge that rates which signals are worth writing about.

Input: list of (SignalResult, GameEntity) — the deterministic detectors fired,
       now we ask the LLM "which of these will actually make a good post?"
Output: list of SignalJudgment, one per input signal.

Cost target: ~$0.001 per call (judging up to ~10 signals at once).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from xhs_agent.config import settings, tuning
from xhs_agent.domain.base import SignalResult
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.llm_tracker import call_llm
from xhs_agent.observability.logger import get_logger
from xhs_agent.prompt.assembler import assemble_signal_judgment_prompt
from xhs_agent.storage.models import LlmPurpose

log = get_logger(__name__)


# Tool schema for batch judgment output — forces well-formed structured output.
JUDGMENT_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "1-based index matching the input list"},
                    "worth_writing": {"type": "boolean"},
                    "template": {
                        "type": "string",
                        "description": "Template name to use. One of: negative_review_burst, hidden_gem, comeback_game, new_release_heat, player_spike_event.",
                    },
                    "angle": {
                        "type": "string",
                        "description": "≤20 字的切入角度 / 钩子",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "≤30 字 为什么值得写 / 不值得写",
                    },
                },
                "required": ["index", "worth_writing", "reasoning"],
            },
        }
    },
    "required": ["results"],
}

JUDGMENT_TOOL_NAME = "submit_judgments"
JUDGMENT_TOOL_DESC = "Submit per-signal worth-writing judgments and recommended templates."


@dataclass
class SignalJudgment:
    signal: SignalResult
    worth_writing: bool
    template: Optional[str]      # one of negative_review_burst / hidden_gem / ...
    angle: Optional[str]         # short hook for content_agent
    reasoning: Optional[str]


def judge_signals(
    signals: list[SignalResult],
    entities_by_id: dict[str, GameEntity],
    pipeline_run_id: Optional[int] = None,
) -> list[SignalJudgment]:
    """Score each signal: should we write about it, and if so, with what angle?"""
    if not signals:
        return []

    prompt = assemble_signal_judgment_prompt(signals, entities_by_id)

    # Cap inputs by truncating to a reasonable size for Haiku
    estimated_in = len(prompt.user_message) // 3  # rough char->token
    estimated_out = 50 * len(signals)             # ~50 tokens per signal

    result = call_llm(
        purpose=LlmPurpose.SIGNAL_JUDGMENT,
        model=settings.model_signal_judgment,
        messages=[{"role": "user", "content": prompt.user_message}],
        system=prompt.system,
        max_tokens=max(400, estimated_out + 200),
        temperature=0.3,  # we want consistent judgment, not creative
        pipeline_run_id=pipeline_run_id,
        estimated_in_tokens=estimated_in,
        estimated_out_tokens=estimated_out,
        tool_schema=JUDGMENT_TOOL_SCHEMA,
        tool_name=JUDGMENT_TOOL_NAME,
        tool_description=JUDGMENT_TOOL_DESC,
    )

    judgments = _parse_response(result.text, signals)

    # Per-judgment debug log so we can see what Haiku decided on each
    for i, j in enumerate(judgments, start=1):
        log.info(
            "judgment_parsed",
            i=i,
            game=j.signal.entity_name,
            sig=j.signal.signal_type,
            severity=j.signal.severity,
            worth=j.worth_writing,
            template=j.template,
            reasoning=(j.reasoning or "")[:60],
        )

    # Fallback: if Haiku rejected everything, force top-N by score.
    # N comes from tuning.judgment.force_approve_when_zero (default 2). Set to 0
    # to disable. Template is chosen by reading the entity's actual data
    # (recent vs historical positive rate, game age) rather than naively mapping
    # signal_type → template — the latter caused player_spike to always go to
    # negative_review_burst even when the game was a buzzy new release with
    # mid-tier ratings (better fit: new_release_heat).
    approved_count = sum(1 for j in judgments if j.worth_writing)
    force_n_target = tuning.judgment.force_approve_when_zero
    if signals and approved_count == 0 and force_n_target > 0:
        sorted_indices = sorted(
            range(len(signals)), key=lambda i: -signals[i].score
        )
        forced = 0
        # Walk top-by-score and only force-approve when we can pick an HONEST
        # content_type. If _infer returns None for a given signal, skip — it
        # means the data is too ambiguous to tell a clean story, and we'd
        # rather output fewer candidates than mislabeled ones.
        for idx in sorted_indices:
            if forced >= force_n_target:
                break
            old = judgments[idx]
            entity = entities_by_id.get(old.signal.entity_id)
            # Gate: don't force-pick games with too few recent reviews — they
            # produce empty/misleading content in the review-analysis templates.
            review_7d = (getattr(entity, "recent_7d_review_count", None) or 0) if entity else 0
            min_for_force = tuning.candidate_selection.force_pick_min_review_count_7d
            if review_7d < min_for_force:
                log.info(
                    "force_pick_skipped_low_reviews",
                    entity=old.signal.entity_name,
                    review_7d=review_7d,
                    min_required=min_for_force,
                )
                continue
            inferred_template = old.template or _infer_template_from_entity(
                old.signal, entity
            )
            if inferred_template is None:
                log.info(
                    "signal_agent_fallback_skipped_ambiguous",
                    entity=old.signal.entity_name,
                    signal_type=old.signal.signal_type,
                    age=getattr(entity, "game_age_days", None),
                )
                continue
            judgments[idx] = SignalJudgment(
                signal=old.signal,
                worth_writing=True,
                template=inferred_template,
                angle=old.angle or _infer_angle(old.signal, entity, inferred_template),
                reasoning="fallback: Haiku approved 0",
            )
            forced += 1

        log.warning(
            "signal_agent_fallback_force_approve",
            forced=forced,
            target=force_n_target,
            reason="haiku_approved_zero",
        )

    log.info(
        "signal_agent_done",
        total=len(signals),
        approved=sum(1 for j in judgments if j.worth_writing),
        cost_usd=round(result.cost_usd, 4),
    )
    return judgments


def _parse_response(text: str, signals: list[SignalResult]) -> list[SignalJudgment]:
    """Parse Haiku's JSON output into SignalJudgments.

    Robust to:
    - markdown code fences (we strip them)
    - dry-run mode where text is the canned single-object placeholder
    - missing keys (filled with safe defaults)
    """
    judgments: list[SignalJudgment] = []

    cleaned = _strip_code_fence(text).strip()

    # Dry-run produces a single object; wrap it so the parse still works.
    parsed: list[dict] = []
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            parsed = data
        elif isinstance(data, dict):
            # Could be {"results": [...]} or a single judgment
            if "results" in data and isinstance(data["results"], list):
                parsed = data["results"]
            else:
                parsed = [data]
    except json.JSONDecodeError as e:
        log.warning("signal_agent_parse_failed", error=str(e), preview=cleaned[:200])
        # Fall back: skip-all judgment
        return [
            SignalJudgment(
                signal=sig,
                worth_writing=False,
                template=None,
                angle=None,
                reasoning="LLM 输出 parse 失败",
            )
            for sig in signals
        ]

    # Map by index back to signals
    by_index: dict[int, dict] = {}
    for item in parsed:
        idx = item.get("index")
        if isinstance(idx, int):
            by_index[idx] = item

    for i, sig in enumerate(signals, start=1):
        item = by_index.get(i, {})
        worth = bool(item.get("worth_writing", False))
        # Dry-run case: angle field absent → use signal type as fallback
        template = item.get("template") or _default_template_for(sig.signal_type)
        angle = item.get("angle")
        reasoning = item.get("reasoning")
        judgments.append(
            SignalJudgment(
                signal=sig,
                worth_writing=worth,
                template=template,
                angle=angle,
                reasoning=reasoning,
            )
        )

    # If by_index was empty but we got at least one parsed dict, apply it to first sig
    # (defensive — handles models that ignore the index convention)
    if not by_index and parsed:
        item = parsed[0]
        if signals:
            judgments[0] = SignalJudgment(
                signal=signals[0],
                worth_writing=bool(item.get("worth_writing", False)),
                template=item.get("template")
                or _default_template_for(signals[0].signal_type),
                angle=item.get("angle"),
                reasoning=item.get("reasoning"),
            )

    return judgments


def _strip_code_fence(text: str) -> str:
    """Remove optional ```json ... ``` wrappers that some models add."""
    # ```json or ``` at start, ``` at end
    text = text.strip()
    fence_re = re.compile(r"^```(?:json|JSON)?\s*\n(.*)\n```\s*$", re.DOTALL)
    m = fence_re.match(text)
    if m:
        return m.group(1)
    return text


def _default_template_for(signal_type: str) -> str:
    """Coarse fallback template — only used when we have no entity context.

    Prefer _infer_template_from_entity() which looks at actual review data
    instead of just the signal type. This map is kept for resilience.
    """
    mapping = {
        "negative_burst": "negative_review_burst",
        "positive_burst": "comeback_game",
        "player_spike": "new_release_heat",   # default for spike-no-context = heat
        "new_release_spike": "new_release_heat",
        "review_surge": "comeback_game",
        "discount_event": "discount_worth_checking",
    }
    return mapping.get(signal_type, "negative_review_burst")


def _infer_template_from_entity(signal, entity) -> Optional[str]:
    """Pick a v5 content_type based on entity data. Return None if no honest fit.

    Returning None means "this candidate doesn't tell a clean story" — caller
    should skip rather than force a wrong template (the 漫威争锋 bug: old
    evergreen game with stable mediocre rating got tagged 新品爆款 because the
    old code defaulted player_spike to new_release_heat).

    Honest matches:
    1. age < 30 days  → new_release_heat (real new game)
    2. recent rate << historical rate (drop > 5%) → negative_review_burst
    3. recent rate >> historical rate (rise > 5%) AND age > 180 → comeback_game
    4. signal_type is negative_burst → negative_review_burst (the signal itself
       already encoded the drop, so any caller-passed entity is fine)
    5. signal_type is new_release_spike → new_release_heat (signal already
       gated by age)

    No match = no honest content_type. Caller (fallback) should skip.
    """
    if entity is None:
        # No entity context — only trust signal types that are self-contained
        if signal.signal_type == "negative_burst":
            return "negative_review_burst"
        if signal.signal_type == "new_release_spike":
            return "new_release_heat"
        return None

    age = entity.game_age_days
    recent_pr = entity.recent_7d_positive_rate
    hist_pr = entity.historical_positive_rate

    # Discount event → always 折扣值不值, regardless of review health
    if signal.signal_type == "discount_event":
        return "discount_worth_checking"

    # New release: only when actually new
    if age is not None and age < 30:
        return "new_release_heat"

    # Compare recent vs historical (only if both available)
    if recent_pr is not None and hist_pr is not None:
        delta = recent_pr - hist_pr
        # Guard: never classify as 差评爆炸 when recent rate is healthy (≥80%)
        if delta < -0.05 and recent_pr < 0.80:
            return "negative_review_burst"
        if delta > 0.05 and age is not None and age > 180:
            return "comeback_game"

    # Specific signal types — only ones that are self-evident
    if signal.signal_type == "negative_burst":
        # Extra guard: confirm recent rate is actually bad
        if recent_pr is not None and recent_pr >= 0.80:
            return None  # signal fired but data doesn't support 差评爆炸
        return "negative_review_burst"
    if signal.signal_type == "new_release_spike":
        return "new_release_heat"

    # No honest match. (Old + player_spike + stable rating = no clean story.)
    # Caller should skip this candidate.
    return None


def _infer_angle(signal, entity, template: str) -> str:
    """One-line angle hint for content_agent when Haiku didn't give us one."""
    if template == "new_release_heat" and entity:
        if entity.current_player_count and entity.current_player_count > 50000:
            return f"在线 {entity.current_player_count}，热度真实但要看玩家说什么"
        return "新游热度真实但要看玩家说什么"
    if template == "negative_review_burst" and entity:
        recent = entity.recent_7d_positive_rate
        hist = entity.historical_positive_rate
        if recent is not None and hist is not None and recent < hist:
            drop = round((hist - recent) * 100, 1)
            return f"近7天好评率跌 {drop}%（历史 {hist:.0%} → 近期 {recent:.0%}）"
        return f"近期差评明显上升（历史好评 {hist:.0%}）" if hist else "近期差评上升"
    if template == "discount_worth_checking" and entity:
        parts = []
        if entity.discount_pct:
            parts.append(f"{entity.discount_pct}% off")
        if entity.final_price:
            parts.append(f"折后 ${entity.final_price}")
        if entity.is_at_historic_low:
            parts.append("接近史低")
        return "、".join(parts) + "，值不值得买？" if parts else "折扣期间值不值得入手？"
    if template == "comeback_game":
        return "口碑反转，对比早期和近期评论"
    return "auto-approved（按 score 取 top）"

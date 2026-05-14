"""LLM call wrapper — the only sanctioned way to talk to Anthropic.

DESIGN.md § 14 invariant: "所有 LLM 调用必须经过 llm_tracker，不允许直接 import
anthropic SDK 调用".

Responsibilities:
- Pre-flight budget check (raises BudgetExceededError on overspend)
- Dry-run mode when no API key (returns canned response, still records)
- Records every call to llm_calls table (tokens, cost, duration, success)
- Stable cost computation from a per-model rate table
- Logs structured events for debugging

Usage:
    from xhs_agent.observability.llm_tracker import call_llm
    from xhs_agent.storage.models import LlmPurpose

    result = call_llm(
        purpose=LlmPurpose.SIGNAL_JUDGMENT,
        model=settings.model_signal_judgment,
        messages=[{"role": "user", "content": "..."}],
        system="You are a strict signal evaluator.",
        max_tokens=500,
    )
    print(result.text)
    print(result.cost_usd)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Optional

from xhs_agent.budget.guard import check_budget
from xhs_agent.config import settings
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import LlmPurpose
from xhs_agent.storage.repositories import LlmCallRepository

log = get_logger(__name__)

# ============================================================
# Pricing table (USD per 1M tokens)
#
# IMPORTANT: These are rough estimates. Verify against
# https://www.anthropic.com/pricing before relying on the budget guard.
# Update here when Anthropic changes pricing.
# ============================================================

PRICING: dict[str, tuple[float, float]] = {
    # model_id -> (input_per_1m_usd, output_per_1m_usd)
    "claude-haiku-4-5":         (1.0,  5.0),
    "claude-haiku-4-5-20251001": (1.0,  5.0),
    "claude-sonnet-4-6":        (3.0, 15.0),
    "claude-opus-4-6":          (15.0, 75.0),
}


def _price_for(model: str) -> tuple[float, float]:
    """Return (input_per_1m, output_per_1m) USD prices.

    Falls back to Sonnet pricing for unknown models so we don't undercount.
    """
    if model in PRICING:
        return PRICING[model]
    log.warning("unknown_model_pricing_fallback", model=model)
    return PRICING["claude-sonnet-4-6"]


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = _price_for(model)
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000


def estimate_cost(model: str, est_in: int, est_out: int) -> float:
    """Pre-flight estimate — used by budget guard before the call goes out."""
    return compute_cost(model, est_in, est_out)


# ============================================================
# Result type
# ============================================================


@dataclass
class LlmResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    success: bool
    error_message: Optional[str] = None
    raw_response: Optional[Any] = None  # for advanced use; usually ignored


# ============================================================
# Anthropic client (lazy)
# ============================================================

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not settings.anthropic_api_key:
        return None
    # Lazy import so dry-run works even without anthropic installed
    import anthropic  # noqa: WPS433

    _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


# ============================================================
# Public entry point
# ============================================================


def call_llm(
    purpose: LlmPurpose,
    model: str,
    messages: list[dict],
    system: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    pipeline_run_id: Optional[int] = None,
    estimated_in_tokens: int = 0,
    estimated_out_tokens: int = 0,
    tool_schema: Optional[dict] = None,
    tool_name: Optional[str] = None,
    tool_description: Optional[str] = None,
) -> LlmResult:
    """Make an LLM call with full bookkeeping.

    Pre-flight:
        1. Estimate cost (or use 0 if not provided) and check budget.
        2. If no API key, return a dry-run canned response.

    Post-flight:
        Record actual tokens/cost/duration to llm_calls table regardless of
        success or failure.

    If `tool_schema` and `tool_name` are provided, uses Anthropic tool-use mode:
    the model is forced to call the named tool with input matching the schema,
    and we serialize the parsed input dict back to JSON for `result.text`. This
    avoids JSON-in-text parsing failures (e.g. unescaped quotes in Chinese content).
    """
    # Hash prompt for debugging — lets us detect "this exact prompt produced
    # weird output" repeats across runs.
    prompt_hash = _hash_messages(messages, system)

    # Budget check (estimated)
    estimated_cost = estimate_cost(model, estimated_in_tokens, estimated_out_tokens)
    check_budget(estimated_cost_usd=estimated_cost)

    client = _get_client()
    if client is None:
        log.warning(
            "llm_dry_run",
            reason="no_anthropic_api_key",
            purpose=purpose.value,
            model=model,
        )
        return _record_and_return(
            purpose=purpose,
            model=model,
            tokens_in=0,
            tokens_out=0,
            duration_ms=0,
            text=_dry_run_response(purpose, tool_schema=tool_schema),
            prompt_hash=prompt_hash,
            pipeline_run_id=pipeline_run_id,
            success=True,
            error_message="dry_run",
        )

    started = time.perf_counter()
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        # Tool-use mode (structured output, no JSON-in-text parsing risk)
        if tool_schema and tool_name:
            kwargs["tools"] = [{
                "name": tool_name,
                "description": tool_description or f"Submit a structured {tool_name}.",
                "input_schema": tool_schema,
            }]
            kwargs["tool_choice"] = {"type": "tool", "name": tool_name}

        response = client.messages.create(**kwargs)
        duration_ms = int((time.perf_counter() - started) * 1000)

        # Extract output: prefer tool_use input if present, else concatenate text blocks
        text = ""
        if tool_schema and tool_name:
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                    # block.input is already a dict; serialize for downstream parsers
                    text = json.dumps(block.input, ensure_ascii=False)
                    break
        if not text:
            text = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        log.info(
            "llm_call_success",
            purpose=purpose.value,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
        )

        return _record_and_return(
            purpose=purpose,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            text=text,
            prompt_hash=prompt_hash,
            pipeline_run_id=pipeline_run_id,
            success=True,
            raw_response=response,
        )

    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.error(
            "llm_call_failed",
            purpose=purpose.value,
            model=model,
            error=str(exc),
            duration_ms=duration_ms,
        )
        return _record_and_return(
            purpose=purpose,
            model=model,
            tokens_in=0,
            tokens_out=0,
            duration_ms=duration_ms,
            text="",
            prompt_hash=prompt_hash,
            pipeline_run_id=pipeline_run_id,
            success=False,
            error_message=str(exc),
        )


# ============================================================
# Helpers
# ============================================================


def _hash_messages(messages: list[dict], system: Optional[str]) -> str:
    h = hashlib.sha256()
    if system:
        h.update(b"SYSTEM:")
        h.update(system.encode("utf-8"))
    for m in messages:
        h.update(b"\n")
        h.update(m.get("role", "").encode("utf-8"))
        h.update(b":")
        content = m.get("content", "")
        if isinstance(content, str):
            h.update(content.encode("utf-8"))
        else:
            # Future-proof for content blocks
            h.update(repr(content).encode("utf-8"))
    return h.hexdigest()[:16]


def _dry_run_response(purpose: LlmPurpose, tool_schema: Optional[dict] = None) -> str:
    """Canned response used in dry-run mode (no API key).

    Designed to be parseable by the agents that consume it, so end-to-end
    pipeline runs can complete without burning real $.
    """
    if purpose == LlmPurpose.SIGNAL_JUDGMENT:
        # Tool-use callers expect {"results": [...]}; non-tool callers tolerate single object.
        if tool_schema:
            return json.dumps({
                "results": [
                    {"index": 1, "worth_writing": True, "template": "negative_review_burst",
                     "angle": "[dry-run]", "reasoning": "[dry-run]"}
                ]
            }, ensure_ascii=False)
        return '{"worth_writing": true, "angle": "negative_review_burst", "reasoning": "[dry-run]"}'
    if purpose == LlmPurpose.TREND_EXTRACTION:
        return "[dry-run] no memes extracted"
    if purpose in (LlmPurpose.COPYWRITING, LlmPurpose.REWRITE):
        return json.dumps({
            "title": "[dry-run] 测试标题",
            "content": "[dry-run] 这是一段干跑模式生成的占位文案。",
            "hashtags": ["#测试", "#dry-run"],
        }, ensure_ascii=False)
    return "[dry-run] " + purpose.value


def _record_and_return(
    *,
    purpose: LlmPurpose,
    model: str,
    tokens_in: int,
    tokens_out: int,
    duration_ms: int,
    text: str,
    prompt_hash: str,
    pipeline_run_id: Optional[int],
    success: bool,
    error_message: Optional[str] = None,
    raw_response: Any = None,
) -> LlmResult:
    cost = compute_cost(model, tokens_in, tokens_out)
    with session_scope() as s:
        LlmCallRepository(s).record(
            pipeline_run_id=pipeline_run_id,
            purpose=purpose,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            prompt_hash=prompt_hash,
            duration_ms=duration_ms,
            success=success,
            error_message=error_message,
        )
    return LlmResult(
        text=text,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        duration_ms=duration_ms,
        success=success,
        error_message=error_message,
        raw_response=raw_response,
    )

"""Daily LLM budget enforcement.

DESIGN.md § 10:
- Soft warning at 80% of daily budget
- Hard stop at 100% — raises BudgetExceededError

The guard is consulted by llm_tracker before every LLM call, so it sits in the
critical path. Reads/writes are cheap (one COALESCE(SUM) over today's llm_calls).
"""

from __future__ import annotations

from xhs_agent.config import settings
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.repositories import LlmCallRepository

log = get_logger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised when today's spend would exceed the daily budget."""


def check_budget(estimated_cost_usd: float = 0.0) -> None:
    """Check whether a planned LLM call would exceed today's budget.

    Args:
        estimated_cost_usd: Cost we project for the upcoming call. Default 0
            means "just check current spend, not projecting forward."

    Raises:
        BudgetExceededError: if current spend + estimate >= 100% of budget.
    """
    with session_scope() as s:
        repo = LlmCallRepository(s)
        spent_today = repo.total_cost_today()

    budget = settings.daily_budget_usd
    projected = spent_today + estimated_cost_usd
    pct = (projected / budget) if budget > 0 else 0.0

    if projected >= budget:
        log.error(
            "budget_exceeded",
            spent_today=round(spent_today, 4),
            estimated=round(estimated_cost_usd, 4),
            budget=budget,
        )
        raise BudgetExceededError(
            f"LLM budget exceeded: ${projected:.4f} / ${budget:.2f}"
        )

    if pct >= 0.8:
        log.warning(
            "budget_near_limit",
            spent_today=round(spent_today, 4),
            estimated=round(estimated_cost_usd, 4),
            budget=budget,
            pct=round(pct * 100, 1),
        )


def get_status() -> dict:
    """Return current budget status — useful for /status command, dashboards, etc."""
    with session_scope() as s:
        spent = LlmCallRepository(s).total_cost_today()
    budget = settings.daily_budget_usd
    return {
        "spent_today_usd": round(spent, 4),
        "budget_usd": budget,
        "remaining_usd": round(max(0.0, budget - spent), 4),
        "pct_used": round((spent / budget * 100) if budget > 0 else 0.0, 1),
    }

"""Observability — logging and LLM call tracking.

Note: llm_tracker is intentionally NOT re-exported here. It depends on
budget.guard, which depends on this package's logger — eager re-export would
create a circular import. Callers should:

    from xhs_agent.observability.llm_tracker import call_llm
"""

from xhs_agent.observability.logger import get_logger

__all__ = ["get_logger"]

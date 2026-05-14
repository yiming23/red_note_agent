"""Domain pack abstraction. See base.py for the contract."""

from xhs_agent.domain.base import (
    Collector,
    CollectorError,
    Domain,
    SignalDetector,
    SignalResult,
    Template,
)

__all__ = [
    "Collector",
    "CollectorError",
    "Domain",
    "SignalDetector",
    "SignalResult",
    "Template",
]

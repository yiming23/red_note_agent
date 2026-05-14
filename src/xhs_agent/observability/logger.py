"""Structured logging via structlog.

Single config point. All modules should:

    from xhs_agent.observability.logger import get_logger
    log = get_logger(__name__)
    log.info("event_name", field1=value1, ...)

Console output is human-readable in local mode; JSON in production mode.
"""

from __future__ import annotations

import logging
import sys

import structlog

from xhs_agent.config import settings

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # Native (non-stdlib) processors — work with PrintLoggerFactory.
    # Logger name is bound into context inside get_logger() instead.
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.env == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        # Pretty colored output for local dev
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str | None = None):
    """Get a configured structlog logger. Call this at module top.

    Idempotent — safe to call from anywhere. The module name (when provided)
    is bound to the log context as `logger=<name>`.
    """
    _configure()
    base = structlog.get_logger()
    return base.bind(logger=name) if name else base

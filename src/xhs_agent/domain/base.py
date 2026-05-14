"""Domain pack abstraction — interfaces for collectors, signal detectors, and templates.

DESIGN.md § 2: "Domain pack 轻抽象 — 接口预留，V0 只实现 game pack".

A "domain" (like games, future movies/books) bundles:
- Collectors: pull raw data from external sources
- SignalDetectors: evaluate raw data and emit prioritized signals worth writing about
- Templates: content shapes (hook + structure + tone) that map to signal types

Adding a new domain means:
1. Implement domain-specific Entity dataclass(es)
2. Subclass Collector / SignalDetector for each source / signal
3. Define a Domain instance that registers them together

The orchestration layer queries domains generically; it doesn't know about Steam.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, Iterable, Optional, TypeVar

# ============================================================
# Type aliases / generics
# ============================================================

# Entity is whatever object a domain considers a "thing being tracked":
# - games domain: a Steam app record
# - future movies domain: a TMDB title record
# Each domain defines its own Entity dataclass; the abstraction stays generic.
Entity = TypeVar("Entity")


# ============================================================
# Signal output (shared across domains)
# ============================================================


@dataclass
class SignalResult:
    """Output of a SignalDetector — what to record in the game_signals table.

    Domain-agnostic; the entity_id is whatever the domain uses (appid, tmdb_id, etc.)
    """

    entity_id: str
    entity_name: str
    signal_type: str          # e.g. "negative_burst" — must match SignalType enum value
    score: float              # 0.0 - 1.0+ relative magnitude
    severity: str             # "urgent" / "normal" / "low" — must match Severity enum
    raw_data: dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================
# Collector
# ============================================================


class Collector(ABC, Generic[Entity]):
    """Pulls data from one external source.

    Implementations should be:
    - Idempotent (running twice yields same data + no side-effects beyond local cache)
    - Resilient (network errors are logged + raised as CollectorError, not silent)
    - Polite (respect rate limits; sleep between calls if the source needs it)
    """

    name: str = "collector"

    @abstractmethod
    def collect(self, **kwargs) -> Iterable[Entity]:
        """Pull a batch of entities. kwargs are collector-specific (e.g. limit, query)."""
        raise NotImplementedError


class CollectorError(RuntimeError):
    """Raised when a collector fails irrecoverably (network, parse, auth)."""


# ============================================================
# SignalDetector
# ============================================================


class SignalDetector(ABC, Generic[Entity]):
    """Evaluates an entity and returns 0 or 1 SignalResult.

    A detector represents ONE signal type (e.g. "negative_burst"). To evaluate
    multiple signals on the same entity, the orchestrator runs N detectors in
    sequence.
    """

    signal_type: str = "abstract"

    @abstractmethod
    def detect(self, entity: Entity) -> Optional[SignalResult]:
        """Return SignalResult if signal triggers, else None."""
        raise NotImplementedError


# ============================================================
# Template
# ============================================================


@dataclass
class Template:
    """Content template — maps to one SignalType (or multiple).

    DESIGN_v5.md § 4 — 6 content_types: 差评爆炸 / 口碑反转 / 小众神作 /
    折扣值不值 / 评论区反差 / 新品爆款.

    Fields:
        name: machine ID (English snake_case); used in DB / lookups.
        content_type: human-facing Chinese name (also DB content_type column).
        matches_signals: which signal_type values this template handles.
        hook: opening line guidance.
        structure: ordered structural beats (each → a page in v5 multi-page output).
        tone: voice description for this content_type.
        notes: extra guidance for content_agent.
        v0_ready: True if this template can fire on current V0 signals;
            False = placeholder, needs new signal detectors (S5/S8).
    """

    name: str
    matches_signals: list[str]
    hook: str
    structure: list[str]
    tone: str
    content_type: str = ""        # Chinese display name; defaults to name if not set
    notes: str = ""
    v0_ready: bool = True

    def __post_init__(self) -> None:
        if not self.content_type:
            self.content_type = self.name


# ============================================================
# Domain registry
# ============================================================


@dataclass
class Domain:
    """A domain pack — bundles collectors, signals, templates for one vertical.

    The orchestrator iterates domains generically. For V0, only the "games"
    domain instance exists (built in domain/games/domain.py).
    """

    name: str                               # "games", "movies", "books", ...
    collectors: list[Collector] = field(default_factory=list)
    detectors: list[SignalDetector] = field(default_factory=list)
    templates: list[Template] = field(default_factory=list)

    def template_for_signal(self, signal_type: str) -> Optional[Template]:
        """Pick the first template that matches this signal type."""
        for t in self.templates:
            if signal_type in t.matches_signals:
                return t
        return None

    def detector_by_type(self, signal_type: str) -> Optional[SignalDetector]:
        for d in self.detectors:
            if d.signal_type == signal_type:
                return d
        return None

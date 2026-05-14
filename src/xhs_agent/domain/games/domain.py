"""GameDomain — the V0 single domain instance.

Registers Steam collector + 5 signal detectors + content templates as one cohesive
"games" domain. Orchestration code only sees the Domain interface and doesn't
need to know about Steam specifically.
"""

from __future__ import annotations

from xhs_agent.domain.base import Domain
from xhs_agent.domain.games.collectors.steam import SteamCollector
from xhs_agent.domain.games.signals import (
    DiscountEventDetector,
    HiddenGemDetector,
    NegativeBurstDetector,
    NewReleaseSpikeDetector,
    PlayerSpikeDetector,
    PlaytimeSplitDetector,
    PositiveBurstDetector,
    ReviewSurgeDetector,
)
from xhs_agent.domain.games.templates import ALL_TEMPLATES


def build_game_domain() -> Domain:
    """Construct the games Domain with default detector / collector instances.

    Note: SteamSpyCollector is intentionally NOT in the collectors list — it's
    used as a per-appid enricher inside SteamCollector, not as a standalone
    batch source.
    """
    return Domain(
        name="games",
        collectors=[SteamCollector()],
        detectors=[
            NegativeBurstDetector(),
            PositiveBurstDetector(),
            PlayerSpikeDetector(baseline_provider=None),  # baseline coming in V1
            NewReleaseSpikeDetector(),
            ReviewSurgeDetector(baseline_provider=None),
            DiscountEventDetector(),
            HiddenGemDetector(),
            PlaytimeSplitDetector(),
        ],
        templates=list(ALL_TEMPLATES),
    )


# Singleton convenience — orchestrator imports this directly.
GAMES_DOMAIN = build_game_domain()

"""Game signal detectors. One module per signal type."""

from xhs_agent.domain.games.signals.discount_event import DiscountEventDetector
from xhs_agent.domain.games.signals.hidden_gem_signal import HiddenGemDetector
from xhs_agent.domain.games.signals.negative_burst import NegativeBurstDetector
from xhs_agent.domain.games.signals.new_release_spike import NewReleaseSpikeDetector
from xhs_agent.domain.games.signals.player_spike import PlayerSpikeDetector
from xhs_agent.domain.games.signals.playtime_split import PlaytimeSplitDetector
from xhs_agent.domain.games.signals.positive_burst import PositiveBurstDetector
from xhs_agent.domain.games.signals.review_surge import ReviewSurgeDetector

ALL_DETECTORS = [
    NegativeBurstDetector,
    PositiveBurstDetector,
    PlayerSpikeDetector,
    NewReleaseSpikeDetector,
    ReviewSurgeDetector,
    DiscountEventDetector,
    HiddenGemDetector,
    PlaytimeSplitDetector,
]

__all__ = [
    "NegativeBurstDetector",
    "PositiveBurstDetector",
    "PlayerSpikeDetector",
    "NewReleaseSpikeDetector",
    "ReviewSurgeDetector",
    "DiscountEventDetector",
    "HiddenGemDetector",
    "PlaytimeSplitDetector",
    "ALL_DETECTORS",
]

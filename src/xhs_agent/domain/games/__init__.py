"""Games domain pack — V0's only implemented domain."""

from xhs_agent.domain.games.domain import GAMES_DOMAIN, build_game_domain
from xhs_agent.domain.games.entity import GameEntity

__all__ = ["GAMES_DOMAIN", "build_game_domain", "GameEntity"]

"""Similar games comparison collector — SteamSpy genre API.

Fetches top games in the same genre as the target game, computes their
positive rates, and returns a ranked list for the comparison chart.

No API key required. Data source: steamspy.com/api.php?request=genre&genre=XXX
"""

from __future__ import annotations

from typing import Optional

import httpx

from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 12.0
_HEADERS = {"User-Agent": "xhs_game_agent/0.1 (research-only)"}
_MIN_REVIEWS = 200      # ignore games with too few reviews
_MAX_SIMILAR = 7        # show at most this many peers on the chart
_MIN_PEERS = 3          # need at least this many peers to render chart

# Steam returns genre descriptions in the request language; SteamSpy always uses English.
_ZH_TO_EN_GENRE: dict[str, str] = {
    "动作": "Action",
    "冒险": "Adventure",
    "独立": "Indie",
    "角色扮演": "RPG",
    "策略": "Strategy",
    "模拟": "Simulation",
    "休闲": "Casual",
    "运动": "Sports",
    "赛车": "Racing",
    "大型多人在线": "Massively Multiplayer",
    "早期体验": "Early Access",
    "免费游玩": "Free to Play",
}


class SimilarGamesCollector:
    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._http = client or httpx.Client(
            base_url="https://steamspy.com",
            timeout=_TIMEOUT,
            headers=_HEADERS,
            follow_redirects=True,
        )

    def enrich_entity(self, entity) -> None:
        """In-place populate entity.similar_games with peer comparison data."""
        genres: list[str] = getattr(entity, "genres", []) or []
        if not genres:
            log.debug("similar_games_no_genres", appid=entity.appid)
            return

        # Translate Chinese genre names to English for SteamSpy
        en_genres = [_ZH_TO_EN_GENRE.get(g, g) for g in genres[:3]]

        target_reviews = (getattr(entity, "total_reviews", None) or 0)

        # Try each genre until we find enough peers
        peers: list[dict] = []
        used_genre = None
        for genre in en_genres:
            raw = self._fetch_genre(genre)
            candidates = self._filter_peers(
                raw, exclude_appid=entity.appid, target_reviews=target_reviews
            )
            if len(candidates) >= _MIN_PEERS:
                peers = candidates[:_MAX_SIMILAR]
                used_genre = genre
                break

        if not peers:
            log.info("similar_games_no_peers", appid=entity.appid, genres=genres,
                     tried_genres=en_genres)
            return

        entity.similar_games = peers
        log.info(
            "similar_games_enriched",
            appid=entity.appid,
            genre=used_genre,
            peers=len(peers),
        )

    def _fetch_genre(self, genre: str) -> dict:
        try:
            resp = self._http.get(
                "/api.php",
                params={"request": "genre", "genre": genre},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("similar_games_fetch_failed", genre=genre, error=str(exc))
            return {}

    def _filter_peers(
        self, raw: dict, exclude_appid: str, target_reviews: int = 0
    ) -> list[dict]:
        peers = []
        for appid_str, info in raw.items():
            if appid_str == str(exclude_appid):
                continue
            pos = info.get("positive", 0) or 0
            neg = info.get("negative", 0) or 0
            total = pos + neg
            if total < _MIN_REVIEWS:
                continue
            # Exclude extreme outliers vs target (CS:GO/PUBG dwarfing indie games)
            if target_reviews > 0:
                ratio = total / target_reviews
                if ratio < 0.1 or ratio > 10.0:
                    continue
            rate = pos / total
            peers.append({
                "name": info.get("name", f"appid {appid_str}"),
                "appid": appid_str,
                "positive_rate": round(rate, 4),
                "total_reviews": total,
            })

        # Sort by total_reviews desc to get well-known representative titles
        peers.sort(key=lambda x: -x["total_reviews"])
        return peers

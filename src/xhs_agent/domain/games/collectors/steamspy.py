"""SteamSpy collector — sales/owner estimates and lifetime player counts.

SteamSpy is a third-party site providing approximate ownership/playtime numbers
based on public profile sampling. The numbers are estimates, not exact, but
plenty good for "does this game have any audience?" judgments.

No API key required. Endpoints: https://steamspy.com/api.php

DESIGN.md § 3.2.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from xhs_agent.config import tuning
from xhs_agent.domain.base import CollectorError
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

URL = "https://steamspy.com/api.php"
DEFAULT_TIMEOUT = 15.0
DEFAULT_HEADERS = {
    "User-Agent": "xhs_game_agent/0.1 (local-dev)",
}


class SteamSpyCollector:
    """Lightweight SteamSpy fetcher.

    Not a Collector subclass — it's a per-appid enricher used alongside SteamCollector,
    not a standalone batch source. Call methods directly on the appids you care about.
    """

    name = "steamspy"

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        request_interval_sec: Optional[float] = None,
    ) -> None:
        # SteamSpy asks for >1 req/sec politeness.
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT, headers=DEFAULT_HEADERS, follow_redirects=True
        )
        if request_interval_sec is None:
            request_interval_sec = tuning.games.collectors.steamspy.request_interval_sec
        self.request_interval_sec = request_interval_sec

    def app_details(self, appid: str) -> dict[str, Any]:
        """Return SteamSpy's per-app details (owners, players_forever, average_forever)."""
        params = {"request": "appdetails", "appid": appid}
        try:
            resp = self._client.get(URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise CollectorError(f"steamspy appdetails {appid} failed: {e}") from e
        finally:
            self._sleep()

        # SteamSpy returns {} when an app is unknown; treat as soft miss
        if not data:
            log.debug("steamspy_unknown_app", appid=appid)
            return {}
        return data

    def top_in_2_weeks(self) -> list[dict[str, Any]]:
        """Top 100 by 2-week active players (approximate)."""
        params = {"request": "top100in2weeks"}
        try:
            resp = self._client.get(URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise CollectorError(f"steamspy top100in2weeks failed: {e}") from e
        finally:
            self._sleep()

        if not isinstance(data, dict):
            return []
        return list(data.values())

    def _sleep(self) -> None:
        if self.request_interval_sec > 0:
            time.sleep(self.request_interval_sec)

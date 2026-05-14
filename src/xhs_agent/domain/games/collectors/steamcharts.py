"""SteamCharts player-count history collector.

Fetches monthly peak player data from steamcharts.com for a given Steam appid.
No API key required. Returns a compact list of monthly records for charting.

API: https://steamcharts.com/app/{appid}/chart-data.json
Response: [[timestamp_ms, avg_players], ...]  — hourly granularity, all-time

We aggregate hourly → monthly (peak per month) and return the last N months.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx

from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 10.0
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; xhs_game_agent/0.1; research-only)",
    "Referer": "https://steamcharts.com/",
}
_MONTHS_TO_KEEP = 24  # show at most last 24 months on the chart


class SteamChartsClient:
    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._http = client or httpx.Client(
            base_url="https://steamcharts.com",
            timeout=_TIMEOUT,
            headers=_HEADERS,
            follow_redirects=True,
        )

    def fetch_monthly_history(self, appid: str) -> list[dict]:
        """Return a list of monthly peak player dicts, oldest → newest.

        Each dict: {"month": "2024-09", "peak": 12345, "avg": 8000}
        Returns [] on any error.
        """
        try:
            resp = self._http.get(f"/app/{appid}/chart-data.json")
            resp.raise_for_status()
            raw: list[list] = resp.json()
        except Exception as exc:
            log.warning("steamcharts_fetch_failed", appid=appid, error=str(exc))
            return []

        if not raw:
            return []

        # Aggregate hourly → monthly (track peak and sum/count for avg)
        monthly: dict[str, dict] = {}
        for entry in raw:
            if len(entry) < 2:
                continue
            ts_ms, players = entry[0], entry[1]
            if players is None:
                continue
            try:
                dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
            key = dt.strftime("%Y-%m")
            if key not in monthly:
                monthly[key] = {"peak": 0, "total": 0, "count": 0}
            monthly[key]["peak"] = max(monthly[key]["peak"], int(players))
            monthly[key]["total"] += int(players)
            monthly[key]["count"] += 1

        records = []
        for month_key in sorted(monthly.keys()):
            m = monthly[month_key]
            avg = round(m["total"] / m["count"]) if m["count"] else 0
            records.append({"month": month_key, "peak": m["peak"], "avg": avg})

        # Trim to last N months (skip trailing months with 0 peak — data gaps)
        records = [r for r in records if r["peak"] > 0]
        return records[-_MONTHS_TO_KEEP:]

    def enrich_entity(self, entity) -> None:
        """In-place populate player_count_history + derived trend fields."""
        records = self.fetch_monthly_history(entity.appid)
        if not records:
            return

        entity.player_count_history = records

        peaks = [r["peak"] for r in records]
        all_time_peak = max(peaks)
        entity.player_count_all_time_peak = all_time_peak

        # Peak month label (e.g. "2021-03")
        peak_idx = peaks.index(all_time_peak)
        entity.player_count_peak_month = records[peak_idx]["month"]

        # Trend: current (last month peak) vs all-time peak
        if all_time_peak > 0 and len(records) >= 2:
            current_peak = records[-1]["peak"]
            pct = round((current_peak - all_time_peak) / all_time_peak * 100, 1)
            entity.player_count_trend_pct = pct  # negative = declined, positive = growing

        log.info(
            "steamcharts_enriched",
            appid=entity.appid,
            months=len(records),
            all_time_peak=all_time_peak,
            peak_month=entity.player_count_peak_month,
            trend_pct=getattr(entity, "player_count_trend_pct", None),
        )

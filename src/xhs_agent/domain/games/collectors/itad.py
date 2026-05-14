"""IsThereAnyDeal (ITAD) price collector.

Uses ITAD v2 API key to fetch Steam price data.
No key → silently returns None; pipeline continues without price data.

API docs: https://docs.isthereanydeal.com/
Endpoints:
  GET /games/lookup/v1?key=KEY&appid=STEAM_APPID  → ITAD game ID
  POST /games/storelow/v2?key=KEY&shops=steam     → all-time lowest on Steam
  GET /games/history/v2?key=KEY&id=GAME_ID&shops=steam → price change events (newest first)
"""

from __future__ import annotations

from typing import Optional

import httpx

from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

_BASE = "https://api.isthereanydeal.com"
_TIMEOUT = 10.0
_HEADERS = {"User-Agent": "xhs_game_agent/0.1 (research-only)"}


class ItadClient:
    def __init__(self, api_key: str, client: Optional[httpx.Client] = None) -> None:
        self._key = api_key
        self._http = client or httpx.Client(
            base_url=_BASE, timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
        )

    def enrich_entity(self, entity) -> None:
        """In-place populate historic_low_price / pct_above_historic_low / is_at_historic_low / price_history."""
        if not entity.is_on_special or entity.final_price is None:
            return

        game_id = self._lookup_game_id(entity.appid)
        if not game_id:
            return

        low = self._get_historic_low(game_id)
        if low is None:
            return

        entity.historic_low_price = low
        if low > 0:
            current = float(entity.final_price)
            pct = round((current - low) / low * 100, 1)
            entity.pct_above_historic_low = max(pct, 0.0)
            entity.is_at_historic_low = pct <= 5.0

        # Also fetch full price change history for the chart
        history = self._get_price_history(game_id)
        if history:
            entity.price_history = history

        log.info(
            "itad_enriched",
            appid=entity.appid,
            low=low,
            current=entity.final_price,
            pct_above=entity.pct_above_historic_low,
            is_at_low=entity.is_at_historic_low,
            history_events=len(history),
        )

    def _lookup_game_id(self, steam_appid: str) -> Optional[str]:
        try:
            resp = self._http.get(
                "/games/lookup/v1",
                params={"key": self._key, "appid": steam_appid},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("itad_lookup_failed", appid=steam_appid, error=str(exc))
            return None

        if not data.get("found"):
            log.debug("itad_game_not_found", appid=steam_appid)
            return None
        return data.get("game", {}).get("id")

    def _get_historic_low(self, game_id: str) -> Optional[float]:
        try:
            resp = self._http.post(
                "/games/storelow/v2",
                params={"key": self._key, "shops": "steam"},
                json=[game_id],
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("itad_storelow_failed", game_id=game_id, error=str(exc))
            return None

        # Response: [{"id": "<game_id>", "lows": [{"shop": {"id": 61, "name": "Steam"}, "price": {...}}, ...]}]
        game_entry = next((item for item in (data or []) if item.get("id") == game_id), None)
        if not game_entry:
            log.debug("itad_no_game_entry", game_id=game_id)
            return None

        lows = game_entry.get("lows") or []
        steam_low = next(
            (r for r in lows if str((r.get("shop") or {}).get("id", "")) == "61"),
            None,
        )
        if steam_low is None:
            # fallback: match by shop name
            steam_low = next(
                (r for r in lows if "steam" in str((r.get("shop") or {}).get("name", "")).lower()),
                None,
            )
        if not steam_low:
            log.debug("itad_no_steam_low", game_id=game_id)
            return None

        price_data = steam_low.get("price") or {}
        low_amount = price_data.get("amount")
        return float(low_amount) if low_amount is not None else None

    def _get_price_history(self, game_id: str) -> list[dict]:
        """Return price change events for Steam, oldest → newest.

        Each dict: {"date": "2024-09", "price": 6.24, "regular": 24.99, "cut": 75}
        Only includes Steam events; non-Steam shops filtered out.
        """
        try:
            resp = self._http.get(
                "/games/history/v2",
                params={"key": self._key, "id": game_id, "shops": "steam"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("itad_history_failed", game_id=game_id, error=str(exc))
            return []

        events = []
        for entry in (data or []):
            shop_id = str((entry.get("shop") or {}).get("id", ""))
            if shop_id != "61":  # Steam shop id
                continue
            ts = entry.get("timestamp", "")
            deal = entry.get("deal") or {}
            price = (deal.get("price") or {}).get("amount")
            regular = (deal.get("regular") or {}).get("amount")
            cut = deal.get("cut", 0)
            if price is None:
                continue
            # Parse "2026-04-14T19:16:38+02:00" → "2026-04"
            date_str = ts[:7] if len(ts) >= 7 else ts
            events.append({
                "date": date_str,
                "price": float(price),
                "regular": float(regular) if regular is not None else float(price),
                "cut": int(cut),
            })

        # API returns newest-first; reverse to oldest-first for charting
        events.reverse()
        return events

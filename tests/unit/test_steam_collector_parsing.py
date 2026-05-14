"""Unit tests for SteamCollector parsing logic.

Uses a stub httpx.Client that serves canned JSON from tests/fixtures/.
Validates that the collector correctly translates API responses into the
shape downstream signal detectors expect, without network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xhs_agent.domain.games.collectors.steam import (
    SteamCollector,
    _days_since_iso,
    _parse_release_date,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ----------------------------------------------------------------
# Stub httpx client
# ----------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _StubClient:
    """Routes URLs to canned JSON fixtures."""

    def __init__(self, route_map: dict[str, Any]) -> None:
        self._routes = route_map
        self.calls: list[str] = []

    def get(self, url: str, params: dict | None = None) -> _StubResponse:
        self.calls.append(url)
        for key, payload in self._routes.items():
            if key in url:
                return _StubResponse(payload)
        return _StubResponse({"error": "no_route"}, status=404)


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------


@pytest.fixture
def stub_steam_client() -> _StubClient:
    top = json.loads((FIXTURES / "steam_top_concurrent_sample.json").read_text())
    featured = json.loads((FIXTURES / "steam_featured_sample.json").read_text())
    return _StubClient({
        "GetGamesByConcurrentPlayers": top,
        "featuredcategories": featured,
        # Per-app responses returned via a generic dict keyed off appid; we'll
        # use simplified responses since collect() also calls appdetails + reviews.
        "appdetails": {
            "730": {
                "success": True,
                "data": {
                    "name": "Counter-Strike 2",
                    "release_date": {"date": "2023-09-27"},
                    "is_free": True,
                },
            }
        },
        # Match any /appreviews/ URL — just return empty summary
        "appreviews": {
            "success": 1,
            "query_summary": {
                "total_reviews": 1000000,
                "total_positive": 850000,
            },
            "reviews": [],
        },
    })


def test_get_top_concurrent_parses_ranks(stub_steam_client):
    collector = SteamCollector(client=stub_steam_client, request_interval_sec=0)
    items = collector.get_top_concurrent(limit=2)
    assert len(items) == 2
    assert items[0]["appid"] == 730
    assert items[0]["concurrent_in_game"] == 1200000


def test_get_featured_returns_categories(stub_steam_client):
    collector = SteamCollector(client=stub_steam_client, request_interval_sec=0)
    featured = collector.get_featured()
    assert "top_sellers" in featured
    assert any(item["id"] == 1086940 for item in featured["top_sellers"])
    assert "new_releases" in featured


def test_parse_release_date_chinese_format():
    assert _parse_release_date("2024 年 9 月 20 日") == "2024-09-20"
    assert _parse_release_date("2023-09-27") == "2023-09-27"
    assert _parse_release_date("Sep 27, 2023") == "2023-09-27"


def test_parse_release_date_unparseable_returns_none():
    assert _parse_release_date("Coming Soon") is None
    assert _parse_release_date("") is None
    assert _parse_release_date(None) is None


def test_days_since_iso_round_trip():
    # Today's date should give 0 days
    from datetime import datetime
    today_iso = datetime.utcnow().strftime("%Y-%m-%d")
    assert _days_since_iso(today_iso) == 0

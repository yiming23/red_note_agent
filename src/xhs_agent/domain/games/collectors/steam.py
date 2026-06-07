"""Steam Web API collector — concurrent players, featured lists, reviews, app details.

Public endpoints, no API key needed. Be polite — sleep between calls.

DESIGN.md § 3.1 documents the endpoints; this module implements them.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx

from xhs_agent.config import tuning
from xhs_agent.domain.base import Collector, CollectorError
from xhs_agent.domain.games.entity import GameEntity
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

# ============================================================
# Endpoints
# ============================================================

URL_TOP_CONCURRENT = (
    "https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/"
)
URL_FEATURED = "https://store.steampowered.com/api/featuredcategories/"
URL_FEATURED_MAIN = "https://store.steampowered.com/api/featured/"
URL_APP_REVIEWS = "https://store.steampowered.com/appreviews/{appid}"
URL_APP_DETAILS = "https://store.steampowered.com/api/appdetails"

DEFAULT_TIMEOUT = 15.0
DEFAULT_HEADERS = {
    "User-Agent": (
        "xhs_game_agent/0.1 "
        "(local-dev; contact: zouyiming6@gmail.com)"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# ============================================================
# Collector
# ============================================================


class SteamCollector(Collector[GameEntity]):
    """Pulls a batch of games (top concurrent + featured) with full review snapshots."""

    name = "steam"

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        request_interval_sec: Optional[float] = None,
    ) -> None:
        # Allow injection for testing; default builds our own client.
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        )
        if request_interval_sec is None:
            request_interval_sec = tuning.games.collectors.steam.request_interval_sec
        self.request_interval_sec = request_interval_sec

        # ITAD client — lazy init; only active when api key is configured
        from xhs_agent.config.settings import settings as _settings
        self._itad = None
        if _settings.itad_api_key:
            from xhs_agent.domain.games.collectors.itad import ItadClient
            self._itad = ItadClient(api_key=_settings.itad_api_key)

        # SteamCharts client — always active (no key required)
        from xhs_agent.domain.games.collectors.steamcharts import SteamChartsClient
        self._steamcharts = SteamChartsClient()

        # Note: SimilarGamesCollector is NOT initialized here — it runs in pipeline.py
        # only for selected candidates, avoiding 60+ SteamSpy calls per run.

    # --------------------------------------------------------
    # Top-level entry point
    # --------------------------------------------------------

    def collect(
        self,
        limit: Optional[int] = None,
        language: Optional[str] = None,
    ) -> Iterable[GameEntity]:
        """Pull games from four pools (top concurrent + new releases + top sellers + specials).

        Each pool contributes up to `limit` appids; after dedup the total is typically
        60-100 unique games — far more signal-diverse than top_concurrent alone.
        """
        if limit is None:
            limit = tuning.games.collectors.steam.default_limit
        if language is None:
            language = tuning.games.collectors.steam.language

        log.info("steam_collect_start", limit=limit)

        top = self.get_top_concurrent(limit=limit)
        featured = self.get_featured()
        featured_main = self.get_featured_main()

        # Build lookup maps from featured lists
        top_seller_appids: dict[str, int] = {
            str(g["id"]): rank
            for rank, g in enumerate(featured.get("top_sellers", []), start=1)
        }
        new_release_appids: set[str] = {
            str(g["id"]) for g in featured.get("new_releases", [])
        }

        # Special/discount metadata: appid -> {discount_pct, original_price, final_price}
        specials_meta: dict[str, dict] = {}
        for g in featured.get("specials", []):
            aid = str(g.get("id") or "")
            if not aid:
                continue
            discount = g.get("discount_percent") or 0
            orig = g.get("original_price")
            final = g.get("final_price")
            specials_meta[aid] = {
                "discount_pct": int(discount),
                "original_price": round(orig / 100, 2) if orig else None,
                "final_price": round(final / 100, 2) if final else None,
            }

        # Merge all four pools preserving insertion order; top_concurrent first
        ordered_appids: list[str] = []
        seen: set[str] = set()

        def _add(appid: str) -> None:
            if appid not in seen:
                seen.add(appid)
                ordered_appids.append(appid)

        # Pool 1: top concurrent
        top_concurrent_appids: dict[str, dict] = {}
        for raw in top:
            aid = str(raw["appid"])
            top_concurrent_appids[aid] = raw
            _add(aid)

        # Pool 2: new releases
        for g in featured.get("new_releases", [])[:limit]:
            _add(str(g.get("id") or ""))

        # Pool 3: top sellers (not already in pool 1+2)
        for g in featured.get("top_sellers", [])[:limit]:
            _add(str(g.get("id") or ""))

        # Pool 4: specials (discounted games)
        for g in featured.get("specials", [])[:limit]:
            _add(str(g.get("id") or ""))

        # Build featured name lookup early (Pool 6 also writes into it)
        featured_names: dict[str, str] = {}
        for key in ("new_releases", "top_sellers", "specials", "coming_soon"):
            for g in featured.get(key, []):
                aid = str(g.get("id") or "")
                if aid and g.get("name"):
                    featured_names[aid] = g["name"]
        for g in featured_main.get("featured_win", []):
            aid = str(g.get("id") or "")
            if aid and g.get("name"):
                featured_names[aid] = g["name"]

        # Pool 5: Steam editorial featured (featured_win — store front-page banner games)
        featured_win_appids: set[str] = set()
        for g in featured_main.get("featured_win", []):
            aid = str(g.get("id") or "")
            if aid:
                featured_win_appids.add(aid)
                _add(aid)

        # Pool 6: SteamSpy top-100-in-2-weeks (active player heat signal)
        steamspy_appids: set[str] = set()
        try:
            from xhs_agent.domain.games.collectors.steamspy import SteamSpyCollector
            spy = SteamSpyCollector()
            for row in spy.top_in_2_weeks()[:limit]:
                aid = str(row.get("appid") or "")
                if aid and aid != "0":
                    steamspy_appids.add(aid)
                    _add(aid)
                    if row.get("name") and aid not in featured_names:
                        featured_names[aid] = row["name"]
        except Exception as exc:
            log.warning("steamspy_pool6_failed", error=str(exc))

        # Remove empty strings that can appear when "id" is missing
        ordered_appids = [a for a in ordered_appids if a and a != "0"]

        log.info("steam_pool_merged", total=len(ordered_appids),
                 top_concurrent=len(top_concurrent_appids),
                 new_releases=len(new_release_appids),
                 top_sellers=len(top_seller_appids),
                 specials=len(specials_meta),
                 featured_win=len(featured_win_appids),
                 steamspy_top100=len(steamspy_appids))

        entities: list[GameEntity] = []
        for appid in ordered_appids:
            raw = top_concurrent_appids.get(appid, {})
            name_fallback = featured_names.get(appid) or f"app_{appid}"
            special = specials_meta.get(appid, {})

            entity = GameEntity(
                appid=appid,
                name=raw.get("name") or name_fallback,
                current_player_count=raw.get("concurrent_in_game") or raw.get("peak_in_game"),
                peak_in_game=raw.get("peak_in_game"),
                store_url=f"https://store.steampowered.com/app/{appid}/",
                review_summary_url=(
                    f"https://store.steampowered.com/appreviews/{appid}?json=1"
                ),
                is_top_seller=appid in top_seller_appids,
                top_seller_rank=top_seller_appids.get(appid),
                is_new_release=appid in new_release_appids,
                is_on_special=appid in specials_meta,
                discount_pct=special.get("discount_pct"),
                original_price=special.get("original_price"),
                final_price=special.get("final_price"),
            )

            # Enrich with details (release date, name, genres, type)
            try:
                details = self.get_app_details(appid, language=language)
                if details:
                    app_type = details.get("type") or ""
                    if app_type and app_type != "game":
                        log.info("steam_skip_non_game", appid=appid,
                                 name=details.get("name"), type=app_type)
                        continue
                    entity.name = details.get("name") or entity.name
                    entity.is_free = bool(details.get("is_free"))
                    rd = details.get("release_date") or {}
                    entity.release_date_iso = _parse_release_date(rd.get("date"))
                    if entity.release_date_iso:
                        entity.game_age_days = _days_since_iso(entity.release_date_iso)
                    raw_genres = details.get("genres") or []
                    entity.genres = [g.get("description", "") for g in raw_genres if g.get("description")]
                    entity.short_description = (details.get("short_description") or "").strip() or None
            except CollectorError as e:
                log.warning("steam_app_details_failed", appid=appid, error=str(e))

            # Reviews — lifetime + recent
            try:
                self._enrich_reviews(entity, language=language)
            except CollectorError as e:
                log.warning("steam_reviews_failed", appid=appid, error=str(e))

            # ITAD price history — only for discounted games (saves API calls)
            if entity.is_on_special and self._itad:
                try:
                    self._itad.enrich_entity(entity)
                except Exception as exc:
                    log.warning("itad_enrich_failed", appid=appid, error=str(exc))

            # SteamCharts player count history — always fetch
            try:
                self._steamcharts.enrich_entity(entity)
            except Exception as exc:
                log.warning("steamcharts_enrich_failed", appid=appid, error=str(exc))

            entities.append(entity)
            self._sleep()

        log.info("steam_collect_done", count=len(entities))
        return entities

    # --------------------------------------------------------
    # Sub-fetchers
    # --------------------------------------------------------

    def get_top_concurrent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return Steam's top-`limit` games by current concurrent in-game count."""
        try:
            resp = self._client.get(URL_TOP_CONCURRENT)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise CollectorError(f"top_concurrent fetch failed: {e}") from e

        # Schema: {"response": {"ranks": [{"rank": 1, "appid": 730, ...}, ...]}}
        ranks = (data.get("response") or {}).get("ranks") or []
        # The endpoint sometimes omits "name" — we'll fill via app_details on enrich step.
        items = []
        for r in ranks[:limit]:
            items.append({
                "appid": r.get("appid"),
                "rank": r.get("rank"),
                "concurrent_in_game": r.get("concurrent_in_game"),
                "peak_in_game": r.get("peak_in_game"),
                "name": r.get("name"),
            })
        self._sleep()
        return items

    def get_featured(self) -> dict[str, Any]:
        """Return featured categories: new_releases, top_sellers, specials, etc.

        cc=cn pins pricing to the China region (CNY) — our audience is CN-based,
        so showing US-region USD prices would be misleading.
        """
        try:
            resp = self._client.get(URL_FEATURED, params={"cc": "cn", "l": "schinese"})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise CollectorError(f"featured fetch failed: {e}") from e

        result: dict[str, Any] = {}
        for cat_key in ("top_sellers", "new_releases", "specials", "coming_soon"):
            cat = data.get(cat_key) or {}
            result[cat_key] = cat.get("items") or []
        self._sleep()
        return result

    def get_featured_main(self) -> dict[str, Any]:
        """Return Steam store front-page featured games (featured_win list)."""
        try:
            resp = self._client.get(URL_FEATURED_MAIN, params={"cc": "cn", "l": "schinese"})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            log.warning("steam_featured_main_failed", error=str(e))
            return {}
        self._sleep()
        return {
            "featured_win": data.get("featured_win") or [],
        }

    def get_app_details(self, appid: str, language: str = "schinese") -> Optional[dict[str, Any]]:
        """Return basic app metadata (name, release_date, type, etc.) or None on miss."""
        params = {"appids": appid, "l": language, "cc": "cn"}
        try:
            resp = self._client.get(URL_APP_DETAILS, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise CollectorError(f"app_details fetch failed for {appid}: {e}") from e

        # Schema: {"<appid>": {"success": true, "data": {...}}}
        wrapper = data.get(str(appid)) or {}
        if not wrapper.get("success"):
            return None
        return wrapper.get("data")

    def get_app_reviews(
        self,
        appid: str,
        filter_: str = "recent",
        review_type: str = "all",
        num_per_page: int = 20,
        language: str = "schinese,english",
        day_range: Optional[int] = None,
        cursor: str = "*",
    ) -> dict[str, Any]:
        """Return raw appreviews payload. `cursor` enables pagination (pass back `data["cursor"]`)."""
        params = {
            "json": 1,
            "filter": filter_,
            "review_type": review_type,
            "num_per_page": num_per_page,
            "language": language,
            "purchase_type": "all",
            "cursor": cursor,
        }
        if day_range:
            params["day_range"] = day_range

        url = URL_APP_REVIEWS.format(appid=appid)
        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise CollectorError(f"reviews fetch failed for {appid}: {e}") from e

        if not data.get("success"):
            raise CollectorError(f"reviews API returned success=0 for {appid}")
        return data

    # --------------------------------------------------------
    # Internals
    # --------------------------------------------------------

    def _enrich_reviews(self, entity: GameEntity, language: str) -> None:
        """Populate review-related fields on a GameEntity by hitting appreviews API."""
        # Lifetime summary
        lifetime = self.get_app_reviews(
            entity.appid,
            filter_="all",
            num_per_page=0,  # we only need the query_summary
            language=language,
        )
        self._sleep()
        summary = lifetime.get("query_summary") or {}
        entity.total_reviews = summary.get("total_reviews")
        entity.total_positive = summary.get("total_positive")
        if entity.total_reviews:
            entity.historical_positive_rate = (
                (entity.total_positive or 0) / entity.total_reviews
            )

        # Recent window — paginate to read more than one page (Steam caps each
        # page at 100). tuning controls how many pages we pull.
        _pages = tuning.games.collectors.steam.review_fetch_pages
        _per_page = tuning.games.collectors.steam.review_fetch_per_page
        reviews: list[dict] = []
        cursor = "*"
        for _ in range(max(1, _pages)):
            recent = self.get_app_reviews(
                entity.appid,
                filter_="recent",
                num_per_page=_per_page,
                language=language,
                cursor=cursor,
            )
            self._sleep()
            batch = recent.get("reviews") or []
            if not batch:
                break
            reviews.extend(batch)
            next_cursor = recent.get("cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        entity.recent_reviews_count = len(reviews)

        now_ts = datetime.now(tz=timezone.utc).timestamp()
        seven_d_ago = now_ts - 7 * 86400
        one_d_ago = now_ts - 86400

        _llm_pool_cap = tuning.games.collectors.steam.review_llm_pool_size

        in_24h = 0
        in_7d = 0
        in_7d_positive = 0
        excerpts: list[str] = []
        llm_pool: list[dict] = []   # text-bearing pool for Review Miner (capped, see tuning)
        stats_pool: list[dict] = [] # metadata-only pool for stats (all reviews)
        lang_counts: dict[str, int] = {}
        lang_voted: dict[str, dict[str, int]] = {}  # language -> {"up": n, "total": n}

        for r in reviews:
            created = r.get("timestamp_created") or 0
            voted_up = bool(r.get("voted_up"))
            author = r.get("author") or {}
            playtime = author.get("playtime_at_review") if isinstance(author, dict) else None
            language = r.get("language") or "unknown"

            if created >= one_d_ago:
                in_24h += 1
            if created >= seven_d_ago:
                in_7d += 1
                if voted_up:
                    in_7d_positive += 1

            # Stats pool: all reviews, metadata only (no text → no LLM cost)
            stats_pool.append({
                "voted_up": voted_up,
                "playtime_minutes": playtime,
                "language": language,
            })
            lang_counts[language] = lang_counts.get(language, 0) + 1
            lv = lang_voted.setdefault(language, {"up": 0, "total": 0})
            lv["total"] += 1
            if voted_up:
                lv["up"] += 1

            text = (r.get("review") or "").strip()
            if text:
                if len(excerpts) < 5:
                    excerpts.append(text[:280])
                if len(llm_pool) < _llm_pool_cap:
                    llm_pool.append({
                        "text": text[:200],
                        "voted_up": voted_up,
                        "playtime_minutes": playtime,
                        "language": language,
                    })

        entity.recent_24h_review_count = in_24h
        entity.recent_7d_review_count = in_7d
        if in_7d > 0:
            entity.recent_7d_positive_rate = in_7d_positive / in_7d
        if reviews:
            up_count = sum(1 for r in reviews if r.get("voted_up"))
            entity.recent_positive_rate = up_count / len(reviews)
        entity.sample_recent_review_excerpts = excerpts
        entity.recent_review_pool = llm_pool
        entity.review_stats_pool = stats_pool
        entity.review_language_dist = lang_counts

        # Per-language positive rate — surfaces "好评率因地区/语言而异" patterns
        # (e.g. CN players love it but JP/RU reviews are bombing it, or vice versa).
        # Only keep languages with enough volume to be meaningful.
        _min_lang_n = tuning.games.collectors.steam.min_reviews_per_language_for_rate
        entity.review_positive_rate_by_language = {
            lang: round(v["up"] / v["total"], 3)
            for lang, v in lang_voted.items()
            if v["total"] >= _min_lang_n
        }

        if stats_pool:
            chinese = lang_counts.get("schinese", 0) + lang_counts.get("tchinese", 0)
            entity.chinese_review_pct = round(chinese / len(stats_pool) * 100, 1)

    def _sleep(self) -> None:
        if self.request_interval_sec > 0:
            time.sleep(self.request_interval_sec)


# ============================================================
# Helpers
# ============================================================


def _parse_release_date(s: Optional[str]) -> Optional[str]:
    """Steam release_date.date can be Chinese-formatted ('2024 年 9 月 20 日') or English.

    Returns ISO 'YYYY-MM-DD' or None on failure.
    """
    if not s:
        return None
    s = s.strip()
    # Try a few common formats Steam uses
    for fmt in ("%Y 年 %m 月 %d 日", "%Y年%m月%d日", "%b %d, %Y", "%d %b, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    log.debug("steam_release_date_parse_miss", raw=s)
    return None


def _days_since_iso(iso_date: str) -> Optional[int]:
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
        return (datetime.utcnow() - d).days
    except ValueError:
        return None

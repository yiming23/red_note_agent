"""DuckDuckGo Lite opinion collector.

Uses lite.duckduckgo.com/lite/ (HTML scraping, no API key needed).
Returns search result snippets as ExternalOpinion objects.

build_ddg_queries() generates signal-aware queries for the Content Director.
"""

from __future__ import annotations

import re
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from xhs_agent.domain.games.collectors.external_opinions import ExternalOpinion
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

_BASE_URL = "https://lite.duckduckgo.com/lite/"
_TIMEOUT = 15.0
_SLEEP_BETWEEN_QUERIES = 1.5
_MAX_RESULTS_PER_QUERY = 8

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


class DuckDuckGoCollector:
    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._http = client or httpx.Client(
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
        )

    def search(
        self,
        queries: list[str],
        max_per_query: int = _MAX_RESULTS_PER_QUERY,
    ) -> list[ExternalOpinion]:
        """Run queries, return deduplicated opinion snippets sorted by rank."""
        seen_urls: set[str] = set()
        all_results: list[ExternalOpinion] = []

        for i, query in enumerate(queries):
            if i > 0:
                time.sleep(_SLEEP_BETWEEN_QUERIES)
            try:
                results = self._run_query(query, max_per_query)
                for r in results:
                    if r.url not in seen_urls:
                        seen_urls.add(r.url)
                        all_results.append(r)
                log.info("ddg_query_done", query=query[:60], results=len(results))
            except Exception as exc:
                log.warning("ddg_query_failed", query=query[:60], error=str(exc))

        return all_results

    def _run_query(self, query: str, max_results: int) -> list[ExternalOpinion]:
        resp = self._http.post(
            _BASE_URL,
            data={"q": query, "kl": "us-en"},
        )
        resp.raise_for_status()
        return self._parse_lite_html(resp.text, max_results)

    def _parse_lite_html(self, html: str, max_results: int) -> list[ExternalOpinion]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[ExternalOpinion] = []
        rank = 0

        # DDG Lite HTML: result links are <a class="result-link">, snippets are in
        # the next <td class="result-snippet"> sibling rows.
        links = soup.find_all("a", class_="result-link")
        snippets = soup.find_all("td", class_="result-snippet")

        for link, snippet in zip(links, snippets):
            if len(results) >= max_results:
                break
            href = link.get("href", "")
            title = link.get_text(strip=True)
            text = snippet.get_text(strip=True)

            combined = f"{title}. {text}".strip(". ") if text else title
            if not combined or len(combined) < 20:
                continue

            rank += 1
            results.append(ExternalOpinion(
                text=combined[:400],
                score=max_results - rank + 1,  # higher rank → higher score
                source="duckduckgo",
                url=href,
            ))

        return results


# ──────────────────────────────────────────────
# Signal-aware query builder
# ──────────────────────────────────────────────

_TEMPLATE_EN_SUFFIXES: dict[str, str] = {
    "negative_review_burst":   "negative reviews controversy problems",
    "new_release_heat":        "review 2025 first impressions",
    "discount_worth_checking": "worth buying sale discount review",
    "comeback_game":           "improved comeback worth playing update",
    "hidden_gem":              "underrated hidden gem review",
    "playtime_contrast":       "hours review worth the time",
}

_TEMPLATE_ZH_SUFFIXES: dict[str, str] = {
    "negative_review_burst":   "差评 问题",
    "new_release_heat":        "评测 好玩吗",
    "discount_worth_checking": "折扣 值得买",
    "comeback_game":           "好评 值得回坑",
    "hidden_gem":              "好玩 推荐",
    "playtime_contrast":       "时长 评价",
}


def build_ddg_queries(
    game_name: str,
    article_template: str,
    key_narrative: str = "",
) -> list[str]:
    """Build 1-2 search queries for a given game + article template.

    Returns an English query and optionally a Chinese query.
    The game_name may be Chinese; the ASCII portion is used for English queries.
    """
    ascii_name = re.sub(r"[^\x00-\x7F]+", "", game_name).strip(" -—：: ")

    en_suffix = _TEMPLATE_EN_SUFFIXES.get(article_template, "review community")
    zh_suffix = _TEMPLATE_ZH_SUFFIXES.get(article_template, "评测 评价")

    queries: list[str] = []

    if ascii_name:
        queries.append(f'"{ascii_name}" {en_suffix}')

    # Always add Chinese query (DDG Lite supports it)
    queries.append(f"{game_name} {zh_suffix}")

    # Deduplicate (in case game_name is already ASCII)
    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            deduped.append(q)

    return deduped

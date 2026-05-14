"""Reddit opinion collector — uses official Reddit OAuth API.

Requires REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET in .env (free "script" app).
How to register: https://www.reddit.com/prefs/apps → create app → script type.

When credentials are absent, enrich_entity() silently returns without appending
to entity.external_opinions, and the downstream opinion section is simply omitted.

Auth: client_credentials grant (read-only, no user login needed).
Token is cached for 55 minutes (Reddit tokens last 60 min).
"""

from __future__ import annotations

import base64
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

from xhs_agent.config import settings
from xhs_agent.domain.games.collectors.external_opinions import ExternalOpinion
from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 15.0
_MIN_SUBREDDIT_SUBSCRIBERS = 5_000
_MAX_POSTS = 12
_MAX_COMMENTS_PER_POST = 3
_MIN_TEXT_LEN = 60
_TOKEN_TTL_SECONDS = 55 * 60   # refresh 5 min before expiry


# Module-level token cache (shared across instances in the same process)
_cached_token: Optional[str] = None
_token_expiry: Optional[datetime] = None


class RedditClient:
    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._http = client or httpx.Client(
            timeout=_TIMEOUT,
            follow_redirects=True,
        )

    def enrich_entity(self, entity) -> None:
        """Append Reddit community opinions to entity.external_opinions.
        Silently returns if Reddit credentials are not configured.
        """
        if not settings.reddit_client_id or not settings.reddit_client_secret:
            log.debug("reddit_skip_no_credentials")
            return

        name: str = getattr(entity, "name", None) or ""
        if not name:
            return

        # Reddit rejects non-ASCII chars in search (e.g. Chinese in game names)
        ascii_name = re.sub(r"[^\x00-\x7F]+", "", name).strip(" -—：: ")
        if not ascii_name:
            log.info("reddit_skip_no_ascii_name", name=name)
            return

        token = self._get_token()
        if not token:
            log.warning("reddit_token_unavailable")
            return

        subreddit = self._find_subreddit(ascii_name, token)
        if not subreddit:
            log.info("reddit_no_subreddit", name=name, searched=ascii_name)
            return

        opinions = self._fetch_opinions(subreddit, token)
        if not opinions:
            log.info("reddit_no_opinions", subreddit=subreddit)
            return

        existing = getattr(entity, "external_opinions", None) or []
        entity.external_opinions = existing + opinions
        log.info(
            "reddit_enriched",
            name=name,
            subreddit=subreddit,
            opinions=len(opinions),
        )

    # ──────────────────────────────────────────────
    # Auth
    # ──────────────────────────────────────────────

    def _get_token(self) -> Optional[str]:
        global _cached_token, _token_expiry
        now = datetime.utcnow()
        if _cached_token and _token_expiry and now < _token_expiry:
            return _cached_token

        credentials = base64.b64encode(
            f"{settings.reddit_client_id}:{settings.reddit_client_secret}".encode()
        ).decode()

        try:
            resp = self._http.post(
                "https://www.reddit.com/api/v1/access_token",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "User-Agent": settings.reddit_user_agent,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=b"grant_type=client_credentials",
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            if not token:
                log.warning("reddit_no_access_token", resp=str(data)[:200])
                return None
            _cached_token = token
            _token_expiry = now + timedelta(seconds=_TOKEN_TTL_SECONDS)
            log.info("reddit_token_acquired")
            return token
        except Exception as exc:
            log.warning("reddit_token_failed", error=str(exc))
            return None

    # ──────────────────────────────────────────────
    # Subreddit discovery
    # ──────────────────────────────────────────────

    def _find_subreddit(self, game_name: str, token: str) -> Optional[str]:
        try:
            resp = self._http.get(
                "https://oauth.reddit.com/search",
                params={"q": game_name, "type": "sr", "limit": 5},
                headers=self._auth_headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            for child in children:
                sr = child.get("data", {})
                subs = sr.get("subscribers", 0) or 0
                if subs >= _MIN_SUBREDDIT_SUBSCRIBERS:
                    sr_name = sr.get("display_name", "")
                    log.debug("reddit_subreddit_found", name=sr_name, subscribers=subs)
                    return sr_name
        except Exception as exc:
            log.warning("reddit_find_subreddit_failed", game=game_name, error=str(exc))
        return None

    # ──────────────────────────────────────────────
    # Post + comment fetching
    # ──────────────────────────────────────────────

    def _fetch_opinions(self, subreddit: str, token: str) -> list[ExternalOpinion]:
        opinions: list[ExternalOpinion] = []
        posts = self._fetch_top_posts(subreddit, token)
        for post in posts:
            data = post.get("data", {})
            title = data.get("title", "")
            body = (data.get("selftext") or "").strip()
            # Skip deleted/removed posts
            if body in ("[deleted]", "[removed]"):
                body = ""
            score = data.get("score", 0) or 0
            post_id = data.get("id", "")
            url = f"https://www.reddit.com{data.get('permalink', '')}"

            combined = f"{title}. {body}".strip(". ") if body else title
            if len(combined) >= _MIN_TEXT_LEN:
                opinions.append(ExternalOpinion(
                    text=combined[:600],
                    score=score,
                    source="reddit",
                    url=url,
                ))

            # Top comments for this post
            if post_id:
                comments = self._fetch_top_comments(subreddit, post_id, token)
                opinions.extend(comments)

            time.sleep(0.3)  # polite rate limit

        opinions.sort(key=lambda o: -o.score)
        return opinions[:15]

    def _fetch_top_posts(self, subreddit: str, token: str) -> list[dict]:
        try:
            resp = self._http.get(
                f"https://oauth.reddit.com/r/{subreddit}/top",
                params={"t": "month", "limit": _MAX_POSTS},
                headers=self._auth_headers(token),
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("children", [])
        except Exception as exc:
            log.warning("reddit_fetch_posts_failed", subreddit=subreddit, error=str(exc))
            return []

    def _fetch_top_comments(
        self, subreddit: str, post_id: str, token: str
    ) -> list[ExternalOpinion]:
        try:
            resp = self._http.get(
                f"https://oauth.reddit.com/r/{subreddit}/comments/{post_id}",
                params={"sort": "top", "limit": _MAX_COMMENTS_PER_POST, "depth": 1},
                headers=self._auth_headers(token),
            )
            resp.raise_for_status()
            listing = resp.json()
            if not isinstance(listing, list) or len(listing) < 2:
                return []
            children = listing[1].get("data", {}).get("children", [])
            results = []
            for child in children:
                d = child.get("data", {})
                body = (d.get("body") or "").strip()
                if body in ("[deleted]", "[removed]"):
                    continue
                score = d.get("score", 0) or 0
                if len(body) >= _MIN_TEXT_LEN:
                    link = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
                    results.append(ExternalOpinion(
                        text=body[:400],
                        score=score,
                        source="reddit",
                        url=link,
                    ))
            return results
        except Exception as exc:
            log.warning("reddit_fetch_comments_failed", post_id=post_id, error=str(exc))
            return []

    def _auth_headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": settings.reddit_user_agent,
        }

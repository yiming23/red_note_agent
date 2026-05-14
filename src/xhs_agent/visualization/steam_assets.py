"""Steam CDN asset fetch + local cache.

Downloads game images from Steam's CDN and caches them under assets/steam_assets/.
Used by the cover renderer to provide game-specific backgrounds.

Asset types and their CDN filenames:
  library_hero  — 3840×1240, best quality for full-bleed backgrounds
  header        — 460×215,   always exists for every appid (safe fallback)
  capsule       — 231×87,    too small for background use

Usage:
    path = fetch_steam_image("570", "library_hero")   # Dota 2
    if path:
        img = Image.open(path)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx

from xhs_agent.observability.logger import get_logger

log = get_logger(__name__)

# Root of project — go up 4 levels from this file: viz/ → xhs_agent/ → src/ → project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _PROJECT_ROOT / "assets" / "steam_assets"

_CDN_BASE = "https://cdn.akamai.steamstatic.com/steam/apps/{appid}/{filename}"

_ASSET_FILENAMES = {
    "library_hero": "library_hero.jpg",
    "header": "header.jpg",
    "capsule": "capsule_616x353.jpg",
}

# Try these in order for cover backgrounds; stop at first success
_COVER_FALLBACK_CHAIN = ["library_hero", "header"]


def fetch_cover_image(appid: str) -> Optional[Path]:
    """Return a local Path to the best available cover image for `appid`.

    Tries library_hero first (best quality), then header (always exists).
    Returns None if all downloads fail.
    """
    for asset_type in _COVER_FALLBACK_CHAIN:
        path = fetch_steam_image(appid, asset_type)
        if path is not None:
            return path
    return None


def fetch_steam_image(appid: str, asset_type: str = "library_hero") -> Optional[Path]:
    """Return a local cached Path for the given asset, downloading if needed.

    Args:
        appid:      Steam appid string (e.g. "570")
        asset_type: one of "library_hero", "header", "capsule"

    Returns:
        Path to local JPEG file, or None on any failure.
    """
    filename = _ASSET_FILENAMES.get(asset_type)
    if filename is None:
        log.warning("steam_asset_unknown_type", asset_type=asset_type)
        return None

    cache_path = _CACHE_DIR / appid / filename
    if cache_path.exists():
        log.debug("steam_asset_cache_hit", appid=appid, asset_type=asset_type)
        return cache_path

    url = _CDN_BASE.format(appid=appid, filename=filename)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                log.debug("steam_asset_not_found", appid=appid, asset_type=asset_type)
                return None
            resp.raise_for_status()
            cache_path.write_bytes(resp.content)
        log.info("steam_asset_downloaded", appid=appid, asset_type=asset_type,
                 bytes=len(resp.content))
        return cache_path
    except Exception as exc:
        log.warning("steam_asset_download_failed", appid=appid, asset_type=asset_type,
                    error=str(exc))
        return None

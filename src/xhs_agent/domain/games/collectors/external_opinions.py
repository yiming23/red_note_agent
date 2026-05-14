"""Shared ExternalOpinion type — used by DuckDuckGoCollector, RedditClient, etc."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExternalOpinion:
    text: str           # snippet or post/comment body
    score: int          # upvotes (Reddit) or inverse rank position (DDG)
    source: str         # "duckduckgo" | "reddit" | "steam_discussion"
    url: str

"""Playtime buckets — group entity.review_stats_pool by play time and sentiment.

Uses the metadata-only stats pool (all fetched reviews) rather than the LLM pool
(capped at 30) so distribution statistics are based on the full sample.

Four buckets (from DESIGN_v5.md § 6):
  short_neg  : < short_hours_max hours, voted_up=False  → 首发劝退型差评
  med_neg    : short..long range,       voted_up=False
  long_neg   : ≥ long_hours_min hours,  voted_up=False  → 后期内容暴雷型差评
  long_pos   : ≥ long_hours_min hours,  voted_up=True   → 核心玩家认可
  other      : short positive or no playtime data
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from xhs_agent.config import tuning

if TYPE_CHECKING:
    from xhs_agent.domain.games.entity import GameEntity


@dataclass
class PlaytimeBucketResult:
    short_neg: int = 0
    med_neg: int = 0
    long_neg: int = 0
    long_pos: int = 0
    other: int = 0
    total: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "short_neg": self.short_neg,
            "med_neg": self.med_neg,
            "long_neg": self.long_neg,
            "long_pos": self.long_pos,
            "other": self.other,
            "total": self.total,
        }

    def short_neg_share(self) -> float:
        return self.short_neg / self.total if self.total else 0.0

    def long_pos_share(self) -> float:
        return self.long_pos / self.total if self.total else 0.0

    def format_for_prompt(self) -> str:
        if self.total == 0:
            return "(无游玩时长分组数据)"
        lines = [f"游玩时长分组（共 {self.total} 条）："]
        lines.append(
            f"- 短时差评（<{tuning.playtime_buckets.short_hours_max}h）: {self.short_neg} 条"
            f"（{round(self.short_neg / self.total * 100)}%）"
        )
        lines.append(f"- 中时差评: {self.med_neg} 条")
        lines.append(
            f"- 长时差评（≥{tuning.playtime_buckets.long_hours_min}h）: {self.long_neg} 条"
            f"（{round(self.long_neg / self.total * 100)}%）"
        )
        long_time_total = self.long_pos + self.long_neg
        long_pos_of_long = round(self.long_pos / long_time_total * 100) if long_time_total else 0
        lines.append(
            f"- 长时好评: {self.long_pos} 条"
            f"（长时玩家中占{long_pos_of_long}%，即{self.long_pos}/{long_time_total}）"
        )
        return "\n".join(lines)


def compute_buckets(entity: "GameEntity") -> PlaytimeBucketResult:
    """Aggregate entity.review_stats_pool into four playtime buckets.

    Falls back to recent_review_pool for backward compatibility.
    """
    result = PlaytimeBucketResult()
    pool = getattr(entity, "review_stats_pool", None) or getattr(entity, "recent_review_pool", None) or []

    short_max_min = tuning.playtime_buckets.short_hours_max * 60
    long_min_min = tuning.playtime_buckets.long_hours_min * 60

    for r in pool:
        result.total += 1
        pt = r.get("playtime_minutes")
        voted = bool(r.get("voted_up", False))

        if pt is None:
            result.other += 1
        elif pt < short_max_min and not voted:
            result.short_neg += 1
        elif pt >= long_min_min and not voted:
            result.long_neg += 1
        elif pt >= long_min_min and voted:
            result.long_pos += 1
        elif not voted:
            result.med_neg += 1
        else:
            result.other += 1

    return result

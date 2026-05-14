"""Unit tests for v5 prohibited_words module — banned-phrase rewriter + illegal scanner."""

from __future__ import annotations

from xhs_agent.utils.prohibited_words import (
    find_illegal,
    find_violations,
    has_blocking_violation,
    rewrite_banned_phrases,
)


# ────────────────────────────────────────────────────────────
# Illegal scanner (block category)
# ────────────────────────────────────────────────────────────


def test_no_illegal_on_clean_text():
    assert find_illegal("这游戏画面真不错，推荐。") == []
    assert not has_blocking_violation("这游戏画面真不错，推荐。")


def test_piracy_hits_block():
    hits = find_illegal("免费破解 GTA V")
    assert any(cat == "piracy" for _, cat in hits)
    assert has_blocking_violation("免费破解 GTA V")


def test_gambling_hits_block():
    assert has_blocking_violation("赌博的玩法")


def test_each_illegal_reported_once():
    hits = find_illegal("外挂下载 外挂下载 外挂下载")
    assert len(hits) == 1


# ────────────────────────────────────────────────────────────
# Banned-phrase rewriter (style/compliance)
# ────────────────────────────────────────────────────────────


def test_rewrite_垃圾游戏():
    text = "这就是个垃圾游戏，别买"
    new_text, applied = rewrite_banned_phrases(text)
    assert "垃圾游戏" not in new_text
    assert any(b == "垃圾游戏" for b, _ in applied)


def test_rewrite_千万别买():
    text = "千万别买"
    new_text, _ = rewrite_banned_phrases(text)
    assert "千万别买" not in new_text
    # Replacement text should contain hint about waiting/observing
    assert "观望" in new_text or "建议" in new_text


def test_rewrite_no_change_on_clean_text():
    text = "这游戏值得放进愿望单"
    new_text, applied = rewrite_banned_phrases(text)
    assert new_text == text
    assert applied == []


def test_rewrite_multiple_phrases():
    text = "千万别买这个垃圾游戏"
    new_text, applied = rewrite_banned_phrases(text)
    assert "千万别买" not in new_text
    assert "垃圾游戏" not in new_text
    assert len(applied) == 2


# ────────────────────────────────────────────────────────────
# Compat shim — find_violations (v4 API)
# ────────────────────────────────────────────────────────────


def test_compat_find_violations_only_returns_illegal():
    """v5: find_violations is alias for find_illegal — marketing/style hits moved
    to rewrite path, so they shouldn't show up here."""
    violations = find_violations("全网最低价")  # marketing word, now rewritten not blocked
    assert violations == []

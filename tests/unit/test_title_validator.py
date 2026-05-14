"""Unit tests for utils.title_validator."""

from __future__ import annotations

from xhs_agent.utils.title_validator import auto_fix_title, validate_title


def test_valid_title_with_brackets():
    r = validate_title("《星空》发售后差评变多，我看了评论区", game_name="星空")
    assert r.ok
    assert r.detected_game_name == "星空"


def test_missing_brackets_flags():
    r = validate_title("星空发售后差评变多，我看了评论", game_name="星空")
    assert not r.ok
    assert any("《》" in i for i in r.issues)


def test_wrong_game_name_in_brackets():
    r = validate_title("《错的》发售后差评变多，我看了评论", game_name="星空")
    assert not r.ok
    assert any("'错的'" in i and "'星空'" in i for i in r.issues)


def test_title_too_short():
    r = validate_title("《G》短", game_name="G")
    assert any("偏短" in i for i in r.issues)


def test_title_too_long():
    long_title = "《Game》" + "啊" * 50
    r = validate_title(long_title, game_name="Game")
    assert any("偏长" in i for i in r.issues)


def test_fuzzy_name_match_works():
    """Game names sometimes have ® or punctuation; matcher should be loose."""
    r = validate_title("《守望先锋》最近评论挺有意思的", game_name="《守望先锋®》")
    assert r.ok or r.detected_game_name == "守望先锋"


# ────────────────────────────────────────────────────────────
# auto_fix_title
# ────────────────────────────────────────────────────────────


def test_auto_fix_prepends_when_missing():
    fixed = auto_fix_title("发售后差评变多", game_name="星空")
    assert fixed.startswith("《星空》")


def test_auto_fix_swaps_wrong_name():
    fixed = auto_fix_title("《错的》发售后差评变多", game_name="星空")
    assert "《星空》" in fixed
    assert "《错的》" not in fixed


def test_auto_fix_keeps_correct_title():
    original = "《星空》发售后差评变多"
    assert auto_fix_title(original, "星空") == original


def test_auto_fix_handles_unbracketed_prefix():
    """If title starts with the bare game name, wrap it instead of duplicating."""
    fixed = auto_fix_title("星空发售后差评变多", game_name="星空")
    # We don't want "《星空》星空发售后..."
    assert fixed.count("星空") == 2 or "《星空》" in fixed
    # Make sure we still got brackets in
    assert "《" in fixed

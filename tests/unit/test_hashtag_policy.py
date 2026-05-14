"""Unit tests for utils.hashtag_policy."""

from __future__ import annotations

from xhs_agent.utils.hashtag_policy import build_hashtags, validate_hashtags


def test_build_includes_fixed_tags():
    r = build_hashtags(content_type="差评爆炸", game_name="星空", genres=[])
    assert "#Steam游戏" in r.tags
    assert "#真实玩家评论" in r.tags


def test_build_includes_content_type_tags():
    r = build_hashtags(content_type="差评爆炸", game_name="星空", genres=[])
    # Should include at least one tag from the 差评爆炸 group
    assert any(t in ("#差评如潮", "#首发翻车", "#游戏避坑", "#Steam差评") for t in r.tags)


def test_build_includes_game_name_tag():
    r = build_hashtags(content_type="差评爆炸", game_name="星空", genres=[])
    assert "#星空" in r.tags


def test_build_translates_genres():
    r = build_hashtags(content_type="小众神作", game_name="A", genres=["Indie", "Strategy"])
    assert "#独立游戏" in r.tags
    assert "#策略游戏" in r.tags


def test_build_respects_total_max():
    r = build_hashtags(
        content_type="差评爆炸",
        game_name="星空",
        genres=["Action", "RPG", "Indie", "Strategy"],
        llm_suggestions=["#玩家吐槽", "#数据分析", "#吐血推荐", "#新游"],
    )
    assert len(r.tags) <= 10


def test_build_no_duplicates():
    r = build_hashtags(
        content_type="差评爆炸",
        game_name="星空",
        genres=[],
        llm_suggestions=["#Steam游戏", "#差评如潮"],  # duplicates with fixed/type
    )
    assert len(r.tags) == len(set(r.tags))


def test_unknown_content_type_still_produces_min_tags():
    r = build_hashtags(content_type="不存在的类型", game_name="星空", genres=[])
    # At least fixed tags + game name
    assert "#Steam游戏" in r.tags
    assert "#星空" in r.tags


# ────────────────────────────────────────────────────────────
# validate_hashtags
# ────────────────────────────────────────────────────────────


def test_validate_below_min():
    result = validate_hashtags(["#Steam游戏", "#测试"])
    assert not result.ok
    assert any("偏少" in i for i in result.issues)


def test_validate_above_max():
    tags = [f"#tag{i}" for i in range(15)]
    result = validate_hashtags(tags)
    assert not result.ok
    assert any("偏多" in i for i in result.issues)


def test_validate_missing_hash_prefix():
    result = validate_hashtags(["nohash", "#ok"])
    assert any("缺少 #" in i for i in result.issues)

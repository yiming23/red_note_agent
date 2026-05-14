"""Unit tests for v5 formatter (banned-phrase rewrite + title/hashtag policy)."""

from __future__ import annotations

from xhs_agent.utils.formatter import format_post


# ────────────────────────────────────────────────────────────
# Basic cleaning (unchanged from v4)
# ────────────────────────────────────────────────────────────


def test_basic_format_returns_clean_full_text():
    fp = format_post(
        title="《测试游戏》最近差评变多，我看了评论",
        content="第一段内容。\n\n第二段内容。",
        hashtags=["游戏", "#测试"],
    )
    assert fp.title.startswith("《测试游戏》")
    assert "第一段内容" in fp.content
    assert all(t.startswith("#") for t in fp.hashtags)
    assert fp.title in fp.full_text


def test_format_strips_markdown_artifacts():
    fp = format_post(
        title="**《Test》最近怎么样？我看了 200 条评论**",
        content="## 错误的标题\n*斜体*正文",
        hashtags=["#标签"],
    )
    assert "**" not in fp.title
    assert "##" not in fp.content


def test_format_collapses_excessive_paragraphs():
    content = "\n\n".join([f"第{i}段内容比较丰富。" for i in range(8)])
    fp = format_post(title="《Game》评测", content=content, hashtags=[])
    paragraphs = fp.content.split("\n\n")
    # persona.paragraphs_max = 6 in v5
    assert len(paragraphs) <= 6


def test_format_dedupes_hashtags():
    fp = format_post(title="t", content="c", hashtags=["#a", "a", "#a", "#b"])
    # Without content_type, hashtag_policy isn't fully applied; just normalization
    assert "#a" in fp.hashtags
    # No duplicates
    assert len(fp.hashtags) == len(set(fp.hashtags))


# ────────────────────────────────────────────────────────────
# v5: banned-phrase rewriting
# ────────────────────────────────────────────────────────────


def test_format_rewrites_banned_phrases_in_content():
    fp = format_post(
        title="《Game》评测",
        content="说真的，这就是个垃圾游戏，千万别买。",
        hashtags=[],
    )
    assert "垃圾游戏" not in fp.content
    assert "千万别买" not in fp.content
    assert any(b == "垃圾游戏" for b, _ in fp.rewrites_applied)


def test_format_rewrites_banned_phrases_in_title():
    fp = format_post(
        title="《Game》必买神作还是垃圾游戏",
        content="正文。",
        hashtags=[],
    )
    assert "必买神作" not in fp.title
    assert "垃圾游戏" not in fp.title


# ────────────────────────────────────────────────────────────
# v5: title validation
# ────────────────────────────────────────────────────────────


def test_title_auto_fix_when_missing_brackets():
    """If game_name is given and title has no《》, formatter should inject it."""
    fp = format_post(
        title="这游戏最近差评爆炸，我看了 1000 条评论",
        content="正文",
        hashtags=[],
        game_name="星空",
    )
    assert "《星空》" in fp.title


def test_title_validation_flags_wrong_game_name():
    fp = format_post(
        title="《错的名字》最近差评爆炸",
        content="正文",
        hashtags=[],
        game_name="星空",
    )
    # auto_fix_title should swap to the correct name
    assert "《星空》" in fp.title


def test_title_length_issues_reported():
    fp = format_post(
        title="《G》短",  # too short
        content="正文",
        hashtags=[],
        game_name="G",
    )
    assert any("偏短" in i for i in fp.title_issues)


# ────────────────────────────────────────────────────────────
# v5: hashtag policy
# ────────────────────────────────────────────────────────────


def test_hashtag_policy_adds_fixed_set_for_known_content_type():
    fp = format_post(
        title="《星空》最近差评变多",
        content="正文",
        hashtags=["#一些LLM的标签"],
        content_type="差评爆炸",
        game_name="星空",
        genres=["Action", "RPG"],
    )
    # Should include some fixed tags
    assert "#Steam游戏" in fp.hashtags
    assert "#游戏值不值得买" in fp.hashtags
    # Should include game-name tag
    assert "#星空" in fp.hashtags
    # Should be within 5-10
    assert 5 <= len(fp.hashtags) <= 10


def test_hashtag_policy_translates_genres():
    fp = format_post(
        title="《G》挺独立的",
        content="正文",
        hashtags=[],
        content_type="小众神作",
        game_name="G",
        genres=["Indie"],
    )
    assert "#独立游戏" in fp.hashtags


# ────────────────────────────────────────────────────────────
# v5: illegal-content blocking
# ────────────────────────────────────────────────────────────


def test_format_detects_blocking_violation_only_for_illegal():
    """Marketing/style hits go through rewrite, not block."""
    fp = format_post(
        title="《G》一般",
        content="100% 全网最低价",  # marketing → just gets normalized through formatting; doesn't block
        hashtags=[],
    )
    # In v5 these are not "blocking" — find_illegal only triggers on adult/gambling/cheat/piracy
    assert not fp.has_blocking_violation


def test_format_detects_blocking_for_piracy():
    fp = format_post(
        title="《Game》",
        content="免费破解版下载链接在这",
        hashtags=[],
    )
    assert fp.has_blocking_violation
    assert any(cat in ("piracy", "cheat") for _, cat in fp.illegal_hits)

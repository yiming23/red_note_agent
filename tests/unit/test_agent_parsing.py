"""Tests for the JSON-output parsing inside signal_agent and content_agent.

We care that:
- markdown code fences get stripped
- malformed JSON falls back gracefully
- dry-run canned responses are interpretable
"""

from __future__ import annotations

from xhs_agent.agents.content_agent import _parse_response as parse_content
from xhs_agent.agents.content_agent import _strip_code_fence
from xhs_agent.agents.signal_agent import _parse_response as parse_signal
from xhs_agent.domain.base import SignalResult


def _fake_signal(idx: int) -> SignalResult:
    return SignalResult(
        entity_id=str(idx),
        entity_name=f"game_{idx}",
        signal_type="negative_burst",
        score=0.8,
        severity="urgent",
    )


def test_signal_parse_array_with_index_mapping():
    text = """[
        {"index": 1, "worth_writing": true, "template": "negative_review_burst", "angle": "差评爆发", "reasoning": "强信号"},
        {"index": 2, "worth_writing": false, "template": null, "angle": null, "reasoning": "数据太少"}
    ]"""
    signals = [_fake_signal(1), _fake_signal(2)]
    judgments = parse_signal(text, signals)
    assert len(judgments) == 2
    assert judgments[0].worth_writing is True
    assert judgments[0].template == "negative_review_burst"
    assert judgments[1].worth_writing is False


def test_signal_parse_handles_code_fence():
    text = """```json
[{"index": 1, "worth_writing": true, "template": "hidden_gem", "angle": "x", "reasoning": "y"}]
```"""
    signals = [_fake_signal(1)]
    judgments = parse_signal(text, signals)
    assert judgments[0].worth_writing is True
    assert judgments[0].template == "hidden_gem"


def test_signal_parse_garbage_falls_back_to_skip_all():
    text = "this is not json"
    signals = [_fake_signal(1), _fake_signal(2)]
    judgments = parse_signal(text, signals)
    assert all(j.worth_writing is False for j in judgments)
    assert all("parse" in (j.reasoning or "") for j in judgments)


def test_signal_parse_dry_run_single_object():
    """Dry-run returns one object — should still produce one judgment."""
    text = '{"worth_writing": true, "angle": "negative_review_burst", "reasoning": "[dry-run]"}'
    signals = [_fake_signal(1)]
    judgments = parse_signal(text, signals)
    assert judgments[0].worth_writing is True


def test_content_parse_full_payload():
    text = '{"title": "标题", "content": "正文", "hashtags": ["游戏", "#测试"]}'
    title, content, tags, err = parse_content(text)
    assert title == "标题"
    assert content == "正文"
    # parser ensures all tags begin with #
    assert all(t.startswith("#") for t in tags)
    assert err is None


def test_content_parse_strips_code_fence():
    text = """```json
{"title": "x", "content": "y", "hashtags": ["#a"]}
```"""
    title, content, _, err = parse_content(text)
    assert title == "x"
    assert content == "y"
    assert err is None


def test_content_parse_handles_missing_fields():
    text = '{"title": "only title"}'
    title, content, tags, err = parse_content(text)
    assert title == "only title"
    assert content == ""
    assert tags == []


def test_content_parse_invalid_json_returns_error():
    title, content, tags, err = parse_content("nope")
    assert title is None
    assert err is not None
    assert "json_decode_error" in err


def test_strip_code_fence_no_fence_passthrough():
    assert _strip_code_fence("plain text") == "plain text"

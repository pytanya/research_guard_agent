"""Unit-тесты парсинга ответа LLM-as-judge."""

import pytest

from src.judge import _parse_score


def test_plain_json():
    text = '{"score": 8.5, "verdict": "good", "criteria": {"structure": 8}, "comment": "ok"}'
    res = _parse_score(text)
    assert res["score"] == 8.5
    assert res["verdict"] == "good"
    assert res["criteria"] == {"structure": 8}


def test_markdown_wrapped_json():
    text = '```json\n{"score": 9, "verdict": "excellent", "criteria": {}, "comment": "c"}\n```'
    res = _parse_score(text)
    assert res["score"] == 9.0
    assert res["verdict"] == "excellent"


def test_bare_score_fallback():
    res = _parse_score("Оценка: 7/10 — неплохо")
    assert res["score"] == 7.0


def test_score_comment_fallback():
    res = _parse_score('Ответ хороший. score: 6.5')
    assert res["score"] == 6.5


def test_invalid_clamped_to_zero():
    res = _parse_score("непонятно")
    assert res["score"] == 0.0


def test_score_clamped_to_10():
    res = _parse_score('{"score": 99, "criteria": {}, "comment": ""}')
    assert res["score"] == 10.0


def test_verdict_inferred_from_score():
    res = _parse_score('{"score": 8.9, "criteria": {}, "comment": ""}')
    assert res["verdict"] == "excellent"

    res = _parse_score('{"score": 3.0, "criteria": {}, "comment": ""}')
    assert res["verdict"] == "poor"

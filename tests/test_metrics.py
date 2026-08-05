"""Unit-тесты MetricsCollector."""

import time

import pytest

from src.metrics import MetricsCollector


def test_empty_metrics():
    m = MetricsCollector()
    d = m.to_dict()
    assert d["success"] is False
    assert d["num_steps"] == 0
    assert d["num_llm_calls"] == 0
    assert d["total_cost_usd"] == 0.0
    assert d["total_tokens"] == 0


def test_cost_accumulation():
    m = MetricsCollector()
    m.add_llm_call(prompt_tokens=1000, completion_tokens=2000, cost_usd=0.001)
    m.add_llm_call(prompt_tokens=500, completion_tokens=500, cost_usd=0.0005)
    assert m.total_cost == pytest.approx(0.0015)
    assert m.num_llm_calls == 2
    assert m.total_tokens == 4000


def test_steps_and_elapsed():
    m = MetricsCollector()
    m.start()
    time.sleep(0.01)
    m.add_step("search_web", duration=1.5, status="OK")
    m.add_step("fetch_url", duration=2.0, status="ERROR", detail="timeout")
    m.stop()
    d = m.to_dict()
    assert d["num_steps"] == 2
    assert d["steps"][0]["tool"] == "search_web"
    assert d["steps"][1]["status"] == "ERROR"
    assert d["elapsed_sec"] >= 0.01
    assert d["steps"][0]["step_num"] == 1
    assert d["steps"][1]["step_num"] == 2


def test_success_flag():
    m = MetricsCollector()
    m.success = True
    m.stop_reason = "completed"
    d = m.to_dict()
    assert d["success"] is True
    assert d["stop_reason"] == "completed"

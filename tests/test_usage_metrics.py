"""LLM 用量聚合器（进程内计数）测试。"""

import json
from types import SimpleNamespace

from astrbot_plugin_livingmemory.core.base import usage_metrics


def _delta(before: dict, after: dict) -> dict:
    return {key: after[key] - before[key] for key in before}


def test_record_usage_from_openai_dict():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage(
        {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        prompt_text="不应参与估算",
        completion_text="不应参与估算",
    )
    after = usage_metrics.get_usage_snapshot()

    assert _delta(before, after) == {
        "calls": 1,
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "estimated_calls": 0,
    }


def test_record_usage_from_token_usage_object():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage(SimpleNamespace(input=80, output=20, total=100))
    after = usage_metrics.get_usage_snapshot()

    delta = _delta(before, after)
    assert delta["input_tokens"] == 80
    assert delta["output_tokens"] == 20
    assert delta["estimated_calls"] == 0


def test_record_usage_total_only_counts_as_input():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage({"total_tokens": 42})
    after = usage_metrics.get_usage_snapshot()

    delta = _delta(before, after)
    assert delta["input_tokens"] == 42
    assert delta["output_tokens"] == 0
    assert delta["estimated_calls"] == 0


def test_estimate_tokens_when_usage_missing():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage(
        None,
        prompt_text="a" * 100,
        completion_text="b" * 10,
    )
    after = usage_metrics.get_usage_snapshot()

    delta = _delta(before, after)
    # 估算比例约 2 字符/token
    assert delta["input_tokens"] == 50
    assert delta["output_tokens"] == 5
    assert delta["estimated_calls"] == 1


def test_unusable_usage_falls_back_to_estimate():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage("not-a-usage", prompt_text="c" * 20)
    after = usage_metrics.get_usage_snapshot()

    delta = _delta(before, after)
    assert delta["input_tokens"] == 10
    assert delta["estimated_calls"] == 1


def test_snapshot_logs_json_every_configured_interval(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(usage_metrics, "_LOG_EVERY_CALLS", 1)
    monkeypatch.setattr(
        usage_metrics.logger, "info", lambda message: logged.append(message)
    )

    usage_metrics.record_llm_usage({"prompt_tokens": 1, "completion_tokens": 2})

    assert len(logged) == 1
    assert logged[0].startswith("[UsageMetrics] ")
    payload = json.loads(logged[0].split(" ", 1)[1])
    assert set(payload) == {
        "calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_calls",
    }
    assert payload["input_tokens"] >= 1
    assert payload["output_tokens"] >= 2

"""LLM 用量聚合器（进程内计数）测试。"""

import json
from types import SimpleNamespace

from astrbot_plugin_livingmemory.core.base import usage_metrics

_NUMERIC_KEYS = (
    "calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_calls",
)


def _delta(before: dict, after: dict, key: str) -> int:
    return after[key] - before[key]


def _provider_delta(before: dict, after: dict) -> dict[str, int]:
    return {
        provider_id: count - before["providers"].get(provider_id, 0)
        for provider_id, count in after["providers"].items()
        if count > before["providers"].get(provider_id, 0)
    }


def test_record_usage_from_openai_dict():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage(
        {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        prompt_text="不应参与估算",
        completion_text="不应参与估算",
        provider_id="provider-a",
    )
    after = usage_metrics.get_usage_snapshot()

    assert {key: _delta(before, after, key) for key in _NUMERIC_KEYS} == {
        "calls": 1,
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "estimated_calls": 0,
    }
    assert _provider_delta(before, after) == {"provider-a": 1}
    assert after["fallback_calls"] == before["fallback_calls"]


def test_record_usage_from_token_usage_object():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage(
        SimpleNamespace(input=80, output=20, total=100),
        provider_id="provider-b",
        fallback_used=True,
    )
    after = usage_metrics.get_usage_snapshot()

    assert _delta(before, after, "input_tokens") == 80
    assert _delta(before, after, "output_tokens") == 20
    assert _delta(before, after, "estimated_calls") == 0
    assert _provider_delta(before, after) == {"provider-b": 1}
    assert _delta(before, after, "fallback_calls") == 1


def test_record_usage_total_only_counts_as_input():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage({"total_tokens": 42}, provider_id="provider-c")
    after = usage_metrics.get_usage_snapshot()

    assert _delta(before, after, "input_tokens") == 42
    assert _delta(before, after, "output_tokens") == 0
    assert _delta(before, after, "estimated_calls") == 0


def test_estimate_tokens_when_usage_missing():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage(
        None,
        prompt_text="a" * 100,
        completion_text="b" * 10,
        provider_id="provider-d",
    )
    after = usage_metrics.get_usage_snapshot()

    # 估算比例约 2 字符/token
    assert _delta(before, after, "input_tokens") == 50
    assert _delta(before, after, "output_tokens") == 5
    assert _delta(before, after, "estimated_calls") == 1


def test_unusable_usage_falls_back_to_estimate():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage("not-a-usage", prompt_text="c" * 20)
    after = usage_metrics.get_usage_snapshot()

    assert _delta(before, after, "input_tokens") == 10
    assert _delta(before, after, "estimated_calls") == 1


def test_missing_provider_id_counts_as_unknown():
    before = usage_metrics.get_usage_snapshot()
    usage_metrics.record_llm_usage({"prompt_tokens": 1, "completion_tokens": 1})
    after = usage_metrics.get_usage_snapshot()

    assert _provider_delta(before, after) == {"unknown": 1}


def test_snapshot_logs_json_every_configured_interval(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(usage_metrics, "_LOG_EVERY_CALLS", 1)
    monkeypatch.setattr(
        usage_metrics.logger, "info", lambda message: logged.append(message)
    )

    usage_metrics.record_llm_usage(
        {"prompt_tokens": 1, "completion_tokens": 2},
        provider_id="log-provider",
        fallback_used=True,
    )

    assert len(logged) == 1
    assert logged[0].startswith("[UsageMetrics] ")
    payload = json.loads(logged[0].split(" ", 1)[1])
    assert set(payload) == {
        "calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_calls",
        "providers",
        "fallback_calls",
    }
    assert payload["input_tokens"] >= 1
    assert payload["output_tokens"] >= 2
    assert payload["providers"].get("log-provider") == 1
    assert payload["fallback_calls"] >= 1

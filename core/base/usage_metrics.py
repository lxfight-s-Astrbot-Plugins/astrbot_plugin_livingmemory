"""进程内 LLM 调用用量聚合（不落盘、无后台线程）。

仅统计当前进程生命周期内的调用次数与 token 数：Provider 响应携带 usage 时
采用真实值，否则按字符数估算（约 2 字符/token），估算次数单独计数以便区分。
"""

import json
from typing import Any

from astrbot.api import logger

# 每累计多少次调用打一条 INFO 日志（英文 JSON，便于日志系统检索）
_LOG_EVERY_CALLS = 50
# 无 usage 时的估算比例：约 2 字符 ≈ 1 token
_CHARS_PER_TOKEN = 2

_calls = 0
_input_tokens = 0
_output_tokens = 0
_estimated_calls = 0


def _coerce_int(value: Any) -> int | None:
    """把数值型字段归一化为非负整数；非数值返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _first_int(*values: Any) -> int | None:
    """返回第一个可归一化的数值字段。"""
    for value in values:
        number = _coerce_int(value)
        if number is not None:
            return number
    return None


def _extract_usage(usage: Any) -> tuple[int, int] | None:
    """把 Provider usage（dict 或对象）归一化为 ``(输入 tokens, 输出 tokens)``。

    兼容 OpenAI 兼容格式（prompt_tokens/completion_tokens/total_tokens）与
    AstrBot 的 TokenUsage（input/output/total）；无法识别时返回 None 走估算。
    """
    if usage is None:
        return None
    if isinstance(usage, dict):
        prompt = _first_int(usage.get("prompt_tokens"), usage.get("input_tokens"))
        completion = _first_int(
            usage.get("completion_tokens"), usage.get("output_tokens")
        )
        total = _first_int(usage.get("total_tokens"))
    else:
        prompt = _first_int(
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "input_tokens", None),
            getattr(usage, "input", None),
        )
        completion = _first_int(
            getattr(usage, "completion_tokens", None),
            getattr(usage, "output_tokens", None),
            getattr(usage, "output", None),
        )
        total = _first_int(
            getattr(usage, "total_tokens", None), getattr(usage, "total", None)
        )
    if prompt is None and completion is None:
        # 只有 total 时无法拆分输入/输出，全部计入输入侧
        return (total, 0) if total is not None else None
    prompt = prompt or 0
    completion = completion or 0
    if total is not None and total > prompt + completion:
        prompt += total - prompt - completion
    return prompt, completion


def record_llm_usage(
    usage: Any = None,
    *,
    prompt_text: str = "",
    completion_text: str = "",
) -> None:
    """记录一次成功的 LLM 调用。

    Args:
        usage: Provider 响应上的 usage（dict 或对象）；None 或无法识别时按文本估算。
        prompt_text: 输入文本，仅在缺少 usage 时用于估算。
        completion_text: 输出文本，仅在缺少 usage 时用于估算。
    """
    global _calls, _input_tokens, _output_tokens, _estimated_calls
    _calls += 1
    counts = _extract_usage(usage)
    if counts is None:
        _input_tokens += len(prompt_text) // _CHARS_PER_TOKEN
        _output_tokens += len(completion_text) // _CHARS_PER_TOKEN
        _estimated_calls += 1
    else:
        _input_tokens += counts[0]
        _output_tokens += counts[1]
    if _calls % _LOG_EVERY_CALLS == 0:
        logger.info(
            f"[UsageMetrics] {json.dumps(get_usage_snapshot(), ensure_ascii=False)}"
        )


def get_usage_snapshot() -> dict[str, int]:
    """返回当前进程的累计用量快照。"""
    return {
        "calls": _calls,
        "input_tokens": _input_tokens,
        "output_tokens": _output_tokens,
        "total_tokens": _input_tokens + _output_tokens,
        "estimated_calls": _estimated_calls,
    }

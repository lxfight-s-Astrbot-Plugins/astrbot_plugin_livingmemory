"""Beta 自主回忆工具的共用辅助：访问解析、身份推导、时间解析、状态输出。"""

import json
from datetime import datetime, time as dt_time
from typing import Any

from ..memory_scope import (
    is_event_memory_allowed,
    resolve_event_identity,
    resolve_memory_scope,
)
from ..utils import get_persona_id
from .memory_evidence_output import fitting_prefix


def json_result(data: dict[str, Any]) -> str:
    """将工具结果稳定序列化为 JSON 文本。"""
    return json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))


def bounded_status(data: dict[str, Any], max_chars: int = 500) -> str:
    """限制无正文的状态结果，避免回显过长查询或错误参数挤爆预算。"""
    payload = dict(data)
    if len(json_result(payload)) > max_chars:
        payload.pop("query", None)
    for key in ("message", "error"):
        if len(json_result(payload)) <= max_chars:
            break
        if key not in payload:
            continue
        text = str(payload[key])

        def fits(value: str) -> bool:
            payload[key] = value
            return len(json_result(payload)) <= max_chars

        payload[key] = fitting_prefix(text, fits)
    return json_result(payload)


async def resolve_tool_access(
    config_manager: Any, plugin_context: Any, wrapper_context: Any
) -> tuple[Any, str | None, str | None, dict[str, Any] | None]:
    """解析工具调用的访问上下文；不允许时返回错误结构。

    Args:
        config_manager: 配置管理器。
        plugin_context: 插件 Context（注册工具时注入），人格解析依赖它的
            conversation_manager/persona_manager，不能传包装层上下文。
        wrapper_context: 工具调用层的 ContextWrapper，仅用于取当前事件。

    Returns:
        (event, recall_session_id, recall_persona_id, error)。error 为 None 表示允许。
    """
    event = wrapper_context.context.event
    if not is_event_memory_allowed(config_manager, event):
        return event, None, None, {"error": "memory access is not allowed"}
    filtering_config = config_manager.filtering_settings
    use_persona_filtering = filtering_config.get("use_persona_filtering", True)
    persona_id = (
        await get_persona_id(plugin_context, event) if use_persona_filtering else None
    )
    recall_session_id = (
        resolve_memory_scope(config_manager, event) or event.unified_msg_origin
    )
    return event, recall_session_id, persona_id, None


def _event_value(event: Any, method_name: str) -> str:
    method = getattr(event, method_name, None)
    if callable(method):
        try:
            return str(method() or "").strip()
        except Exception:
            return ""
    return ""


def derive_speaker_identity(
    config_manager: Any, event: Any
) -> tuple[str | None, list[str], str]:
    """从运行事件推导当前发言人身份键与辅助候选。

    身份只来自事件本身；别名仅作为辅助线索，不会跨平台/账号合并。
    返回 (identity_key, candidates, status)；无可靠 sender 时 status 为
    "unavailable"，调用方应返回 identity_unavailable 而不是猜测。
    """
    sender_id = _event_value(event, "get_sender_id")
    sender_name = _event_value(event, "get_sender_name")
    platform = _event_value(event, "get_platform_name").casefold()
    if not sender_id:
        return None, [], "unavailable"
    identity_key = f"{platform}:{sender_id}" if platform else sender_id
    canonical = resolve_event_identity(config_manager, event) or ""
    keys = [identity_key]
    if canonical and canonical != sender_id:
        keys.append(f"{platform}:{canonical}" if platform else canonical)
    names = [
        value for value in (canonical, sender_name) if value and value != sender_id
    ]
    candidates = list(dict.fromkeys([*keys, *names]))
    return identity_key, candidates, "ok"


def parse_time_bound(value: Any, *, is_end: bool = False) -> float | None:
    """把 ISO 日期/日期时间解析为秒级时间戳；纯日期按 is_end 取当天末/首。

    与引擎来源时间解析保持一致：无时区信息时按本地时间解释。
    无法解析时抛出 ValueError，由调用方转成工具错误返回。
    """
    text = str(value or "").strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"unsupported time format: {value}")
    if parsed.tzinfo is None and is_end and len(text) <= 10:
        parsed = datetime.combine(parsed.date(), dt_time.max.replace(microsecond=0))
    return parsed.timestamp()

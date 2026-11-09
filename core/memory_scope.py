"""Resolve access control, user aliases, and memory isolation scopes."""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

GLOBAL_MEMORY_SCOPE = "livingmemory:global"
CHANNEL_SCOPE_PREFIX = "livingmemory:channel"

# 一个检索请求可携带单个作用域，或「个人 + 频道」等多个作用域的并集。
MemoryScopeFilter = str | Sequence[str] | None


@dataclass(frozen=True)
class AdapterAccess:
    """Adapter ownership and allowed instances for one request."""

    adapter_id: str
    adapter_ids: tuple[str, ...] | None


current_adapter_access: ContextVar[AdapterAccess | None] = ContextVar(
    "livingmemory_adapter_access", default=None
)


@contextmanager
def adapter_access(config: Any, event: Any, *, write: bool = False):
    """Apply adapter policy to this task and reset it when the request finishes."""
    adapter_id = (
        event if isinstance(event, str) else _event_value(event, "get_platform_id")
    )
    if not adapter_id:
        adapter_id = _event_value(event, "unified_msg_origin").partition(":")[0]
    shared = _config_get(config, "filtering_settings.shared_adapter_ids")
    allowed = None
    if write:
        allowed = (adapter_id,)
    elif shared is not None:
        members = parse_value_list(shared)
        allowed = tuple(sorted(members)) if adapter_id in members else (adapter_id,)
    token = current_adapter_access.set(AdapterAccess(adapter_id, allowed))
    try:
        yield
    finally:
        current_adapter_access.reset(token)


def adapter_sql(metadata: str) -> tuple[str, list[str]]:
    """Build a parameterized ownership predicate for an internal SQL expression."""
    access = current_adapter_access.get()
    if access is None or access.adapter_ids is None:
        return "1", []
    values = list(access.adapter_ids)
    return (
        f"json_extract({metadata}, '$.adapter_id') IN ({','.join('?' for _ in values)})",
        values,
    )


def adapter_visible(metadata: dict[str, Any]) -> bool:
    """Apply the current adapter boundary to a loaded memory."""
    access = current_adapter_access.get()
    return (
        access is None
        or access.adapter_ids is None
        or metadata.get("adapter_id") in access.adapter_ids
    )


def adapter_cache_key() -> tuple[str, ...] | None:
    """Return the adapter dimension for request-independent retrieval caches."""
    access = current_adapter_access.get()
    return access.adapter_ids if access is not None else None


def infer_adapter_id(
    metadata: dict[str, Any], sources=(), instances=None
) -> str | None:
    """Recover ownership from structured provenance without matching user IDs."""
    if metadata.get("adapter_id"):
        return str(metadata["adapter_id"])
    raw_sources = [item.get("session_id") for item in sources if isinstance(item, dict)]
    if not raw_sources:
        window = metadata.get("source_window") or {}
        raw_sources = [metadata.get("source_session_id") or window.get("session_id")]
    if not any(raw_sources):
        raw_sources = [metadata.get("old_session_id") or metadata.get("session_id")]
    owners = set()
    for value in raw_sources:
        parts = str(value or "").split(":", 2)
        if len(parts) != 3 or parts[0] == "livingmemory":
            continue
        prefix = parts[0]
        if instances and prefix not in instances:
            matching = [key for key, kind in instances.items() if kind == prefix]
            if len(matching) > 1:
                return None
            if matching:
                prefix = matching[0]
        owners.add(prefix)
    return owners.pop() if len(owners) == 1 else None


def _config_get(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        current: Any = config
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            pass
    return default


def parse_value_list(value: Any) -> list[str]:
    """Parse comma, semicolon, or newline separated configuration values."""
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;\n]", str(value or ""))
    return list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def parse_identity_aliases(value: Any) -> dict[str, str]:
    """Parse one ``source=canonical name`` mapping per line."""
    aliases: dict[str, str] = {}
    for line in str(value or "").splitlines():
        source, separator, target = line.partition("=")
        if not separator:
            continue
        source = source.strip()
        target = target.strip()
        if source and target:
            aliases[source.casefold()] = target
    return aliases


def _event_value(event: Any, method_name: str, attribute_name: str = "") -> str:
    method = getattr(event, method_name, None)
    if callable(method):
        try:
            value = method()
            return str(value).strip() if value is not None else ""
        except Exception:
            return ""
    value = getattr(event, attribute_name or method_name, "")
    return str(value).strip() if value is not None else ""


def resolve_event_identity(config: Any, event: Any) -> str:
    """Return the configured canonical identity for the event sender."""
    sender_id = _event_value(event, "get_sender_id", "sender_id")
    sender_name = _event_value(event, "get_sender_name", "sender_name")
    platform = _event_value(event, "get_platform_name", "platform").casefold()
    aliases = parse_identity_aliases(
        _config_get(config, "access_control.identity_aliases", "")
    )
    candidates = (
        f"{platform}:{sender_id}" if platform and sender_id else "",
        sender_id,
        sender_name,
    )
    for candidate in candidates:
        if candidate and candidate.casefold() in aliases:
            return aliases[candidate.casefold()]
    return (
        sender_id
        or sender_name
        or _event_value(event, "unified_msg_origin", "unified_msg_origin")
    )


def resolve_sender_alias(
    aliases_value: Any,
    platform: str,
    sender_id: str,
    sender_name: str | None,
) -> str | None:
    aliases = parse_identity_aliases(aliases_value)
    for candidate in (f"{platform}:{sender_id}", sender_id, sender_name or ""):
        if candidate and candidate.casefold() in aliases:
            return aliases[candidate.casefold()]
    return sender_name


@dataclass(frozen=True, slots=True)
class EventMemoryAccess:
    """一次事件请求内解析出的白名单、人格与记忆作用域。"""

    allowed: bool
    persona_id: str | None
    scope: str | None
    channel_scope: str | None = None


async def resolve_event_memory_access(
    config: Any, context: Any, event: Any
) -> EventMemoryAccess:
    """一次解析事件的白名单、人格过滤与记忆作用域。

    Args:
        config: ConfigManager 或宿主配置。
        context: 插件 Context；人格解析依赖其 conversation/persona manager。
        event: 当前消息事件。

    Returns:
        白名单不通过时 ``allowed=False``，其余字段为 None。
    """
    if not is_event_memory_allowed(config, event):
        return EventMemoryAccess(allowed=False, persona_id=None, scope=None)
    persona_id = None
    if _config_get(config, "filtering_settings.use_persona_filtering", True):
        from .utils import get_persona_id

        persona_id = await get_persona_id(context, event)
    return EventMemoryAccess(
        allowed=True,
        persona_id=persona_id,
        scope=resolve_memory_scope(config, event),
        channel_scope=resolve_channel_memory_scope(config, event),
    )


def is_channel_scope(scope: Any) -> bool:
    """Return True when *scope* is a shared channel memory scope."""
    return isinstance(scope, str) and scope.startswith(f"{CHANNEL_SCOPE_PREFIX}:")


def channel_scope_parts(scope: Any) -> tuple[str | None, str | None]:
    """Split a channel scope into ``(platform, group_id)``.

    Args:
        scope: Candidate channel scope.

    Returns:
        ``(platform, group_id)``; both are None when *scope* is not a channel.
    """
    if not is_channel_scope(scope):
        return None, None
    parts = str(scope).split(":", 3)
    if len(parts) != 4:
        return None, None
    return parts[2] or None, parts[3] or None


def _is_group_event(event: Any) -> bool:
    group_id = _event_value(event, "get_group_id", "group_id")
    if group_id:
        return True
    message_type = getattr(event, "get_message_type", None)
    if callable(message_type):
        try:
            value = message_type()
        except Exception:
            value = None
        if str(getattr(value, "value", value) or "").casefold() == "group_message":
            return True
    origin = _event_value(event, "unified_msg_origin", "unified_msg_origin")
    return "groupmessage" in origin.casefold()


def resolve_channel_memory_scope(config: Any, event: Any) -> str | None:
    """Resolve the shared group channel scope when channel memory is enabled.

    Args:
        config: ConfigManager or host config.
        event: Current message event.

    Returns:
        ``livingmemory:channel:<platform>:<group_id>`` for group events with a
        resolvable group id, otherwise None.
    """
    if not _config_get(config, "channel_memory.enabled", False):
        return None
    if not _is_group_event(event):
        return None
    group_id = _event_value(event, "get_group_id", "group_id")
    if not group_id:
        origin = _event_value(event, "unified_msg_origin", "unified_msg_origin")
        if "GroupMessage" in origin:
            group_id = origin.rsplit(":", 1)[-1]
        elif "groupmessage" in origin.casefold():
            group_id = origin.rsplit(":", 1)[-1]
    if not group_id:
        return None
    if _scope_mode(config) == "global":
        return None
    session_id = _event_value(event, "unified_msg_origin", "unified_msg_origin")
    isolated = set(
        parse_value_list(
            _config_get(config, "filtering_settings.isolated_sessions", "")
        )
    )
    if session_id and session_id in isolated:
        return None
    platform = _event_value(event, "get_platform_name", "platform").casefold()
    return f"{CHANNEL_SCOPE_PREFIX}:{platform or 'unknown'}:{group_id}"


def _scope_mode(config: Any) -> str:
    return str(
        _config_get(config, "filtering_settings.memory_scope_mode", "legacy")
    ).casefold()


def normalize_memory_scopes(scopes: MemoryScopeFilter) -> list[str] | None:
    """Normalize one scope or a scope sequence into a unique ordered list.

    Args:
        scopes: A single scope string, a sequence of scopes, or None.

    Returns:
        Ordered unique scopes, or None when the filter is empty.
    """
    if scopes is None:
        return None
    if isinstance(scopes, str):
        values = [scopes.strip()] if scopes.strip() else []
    else:
        values = [str(value).strip() for value in scopes if value]
    unique = list(dict.fromkeys(value for value in values if value))
    return unique or None


def merge_memory_scopes(*scopes: MemoryScopeFilter) -> str | list[str] | None:
    """Merge scope filters, preserving order and collapsing a single scope.

    Args:
        *scopes: Scope filters to merge (each may itself be a sequence).

    Returns:
        A single scope string, a non-empty scope list, or None.
    """
    merged: list[str] = []
    for scope in scopes:
        for value in normalize_memory_scopes(scope) or []:
            if value not in merged:
                merged.append(value)
    if not merged:
        return None
    return merged[0] if len(merged) == 1 else merged


def memory_scope_sql(
    column: str, scope_filter: MemoryScopeFilter
) -> tuple[str, list[str]]:
    """Build a SQL predicate for one or more memory scopes.

    Args:
        column: SQL expression holding the stored scope (e.g. ``ge.session_id``).
        scope_filter: Single scope or scope sequence.

    Returns:
        ``(predicate, params)``; predicate is empty when no scope filter is set.
    """
    scopes = normalize_memory_scopes(scope_filter)
    if not scopes:
        return "", []
    if len(scopes) == 1:
        return f"{column} = ?", scopes
    placeholders = ",".join("?" for _ in scopes)
    return f"{column} IN ({placeholders})", scopes


def memory_scope_visible(
    metadata: dict[str, Any], scope_filter: MemoryScopeFilter
) -> bool:
    """Check loaded metadata against a scope filter."""
    scopes = normalize_memory_scopes(scope_filter)
    return scopes is None or metadata.get("session_id") in scopes


def is_event_memory_allowed(config: Any, event: Any) -> bool:
    """Apply the plugin-level allowlist consistently to every entry point."""
    if not _config_get(config, "access_control.whitelist_enabled", False):
        return True
    allowed = {
        value.casefold()
        for value in parse_value_list(
            _config_get(config, "access_control.allowed_ids", "")
        )
    }
    if not allowed:
        return False

    sender_id = _event_value(event, "get_sender_id", "sender_id")
    platform = _event_value(event, "get_platform_name", "platform")
    session_id = _event_value(event, "unified_msg_origin", "unified_msg_origin")
    group_id = _event_value(event, "get_group_id", "group_id")
    identity = resolve_event_identity(config, event)
    candidates = {
        sender_id,
        identity,
        session_id,
        group_id,
        f"{platform}:{sender_id}" if platform and sender_id else "",
    }
    return any(
        candidate and candidate.casefold() in allowed for candidate in candidates
    )


def resolve_user_memory_scope(config: Any, event: Any) -> str:
    """构造用户级作用域；身份别名与运行时读写共用同一份配置。"""
    platform = _event_value(event, "get_platform_name", "platform").casefold()
    identity = resolve_event_identity(config, event).casefold()
    return f"livingmemory:user:{platform or 'unknown'}:{identity}"


def resolve_memory_scope(config: Any, event: Any) -> str | None:
    """Resolve the retrieval/storage scope while preserving legacy defaults."""
    session_id = _event_value(event, "unified_msg_origin", "unified_msg_origin")
    isolated_sessions = set(
        parse_value_list(
            _config_get(config, "filtering_settings.isolated_sessions", "")
        )
    )
    if session_id in isolated_sessions:
        return session_id

    mode = str(
        _config_get(config, "filtering_settings.memory_scope_mode", "legacy")
    ).casefold()
    if mode == "session":
        return session_id
    if mode == "global":
        return GLOBAL_MEMORY_SCOPE
    if mode == "user":
        return resolve_user_memory_scope(config, event)

    use_session = bool(
        _config_get(config, "filtering_settings.use_session_filtering", True)
    )
    if use_session:
        return session_id
    return GLOBAL_MEMORY_SCOPE if isolated_sessions else None


def is_suspicious_session_id(session_id: str | None) -> bool:
    """Detect error strings leaked into unified_msg_origin.

    Some platform adapters surface initialization errors through the message
    origin; memory writes/recalls tagged with such an id would be unreachable
    in later sessions, so entry points log a warning when they see one.

    Args:
        session_id: unified_msg_origin of the current event.

    Returns:
        bool: True when the id looks like a leaked error string.
    """
    return bool(session_id) and "error:" in str(session_id).lower()


__all__ = [
    "CHANNEL_SCOPE_PREFIX",
    "EventMemoryAccess",
    "GLOBAL_MEMORY_SCOPE",
    "MemoryScopeFilter",
    "channel_scope_parts",
    "is_channel_scope",
    "memory_scope_sql",
    "memory_scope_visible",
    "merge_memory_scopes",
    "normalize_memory_scopes",
    "resolve_channel_memory_scope",
    "resolve_event_memory_access",
    "is_event_memory_allowed",
    "is_suspicious_session_id",
    "parse_identity_aliases",
    "parse_value_list",
    "resolve_event_identity",
    "resolve_memory_scope",
    "resolve_user_memory_scope",
    "resolve_sender_alias",
]

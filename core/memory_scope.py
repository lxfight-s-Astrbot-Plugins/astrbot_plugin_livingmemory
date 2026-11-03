"""Resolve access control, user aliases, and memory isolation scopes."""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

GLOBAL_MEMORY_SCOPE = "livingmemory:global"


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
        platform = _event_value(event, "get_platform_name", "platform").casefold()
        identity = resolve_event_identity(config, event).casefold()
        return f"livingmemory:user:{platform or 'unknown'}:{identity}"

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
    "GLOBAL_MEMORY_SCOPE",
    "is_event_memory_allowed",
    "is_suspicious_session_id",
    "parse_identity_aliases",
    "parse_value_list",
    "resolve_event_identity",
    "resolve_memory_scope",
    "resolve_sender_alias",
]

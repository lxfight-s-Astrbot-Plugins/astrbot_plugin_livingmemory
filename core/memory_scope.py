"""Resolve access control, user aliases, and memory isolation scopes."""

from __future__ import annotations

import re
from typing import Any

GLOBAL_MEMORY_SCOPE = "livingmemory:global"


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
    return sender_id or sender_name or _event_value(
        event, "unified_msg_origin", "unified_msg_origin"
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


__all__ = [
    "GLOBAL_MEMORY_SCOPE",
    "is_event_memory_allowed",
    "parse_identity_aliases",
    "parse_value_list",
    "resolve_event_identity",
    "resolve_memory_scope",
    "resolve_sender_alias",
]

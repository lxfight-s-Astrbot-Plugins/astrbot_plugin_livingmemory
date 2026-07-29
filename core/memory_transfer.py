"""Portable memory import/export format helpers."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any

from .memory_source import serialize_source_messages
from .utils.number_utils import clamp_float

TRANSFER_FORMAT = "livingmemory"
TRANSFER_SCHEMA_VERSION = 1
MAX_IMPORT_ENTRIES = 10_000

_CONTENT_KEYS = ("content", "text", "summary", "memory", "value")
_SOURCE_KEYS = (
    "source_messages",
    "messages",
    "conversation",
    "dialogue",
    "dialog",
)
_LONG_TERM_KEYS = (
    "memories",
    "long_term_memories",
    "longTermMemories",
    "long_term_memory",
)
_SHORT_TERM_KEYS = (
    "short_term_memories",
    "shortTermMemories",
    "conversations",
    "sessions",
)


@dataclass(slots=True)
class ImportEntry:
    """One normalized memory ready for preview or import."""

    source_index: int
    content: str = ""
    importance: float = 0.5
    session_id: str | None = None
    persona_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source_messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def requires_summary(self) -> bool:
        return not self.content and len(self.source_messages) >= 2


def memory_import_key(
    content: str, session_id: str | None, persona_id: str | None
) -> tuple[str, str, str]:
    """Build the exact duplicate key used by preview and import."""
    return (
        " ".join(str(content or "").split()),
        str(session_id or ""),
        str(persona_id or ""),
    )


def parse_transfer_content(
    content: str, format_hint: str = "json"
) -> tuple[list[ImportEntry], list[dict[str, Any]]]:
    """Parse native or common external JSON/CSV into normalized entries."""
    hint = str(format_hint or "json").strip().lower()
    if hint == "csv":
        raw_entries = [
            _unescape_csv_row(row)
            for row in csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        ]
    elif hint == "json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc.msg}") from exc
        raw_entries = _extract_json_entries(payload)
    else:
        raise ValueError("仅支持 JSON 或 CSV 格式")

    if len(raw_entries) > MAX_IMPORT_ENTRIES:
        raise ValueError(f"单次最多导入 {MAX_IMPORT_ENTRIES} 条记忆")

    entries: list[ImportEntry] = []
    errors: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_entries):
        try:
            entries.append(_normalize_entry(raw, index))
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})
    return entries, errors


def serialize_transfer_json(records: list[dict[str, Any]], exported_at: str) -> str:
    """Serialize records using the native round-trip JSON schema."""
    return json.dumps(
        {
            "format": TRANSFER_FORMAT,
            "schema_version": TRANSFER_SCHEMA_VERSION,
            "exported_at": exported_at,
            "memory_count": len(records),
            "memories": records,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def serialize_transfer_csv(records: list[dict[str, Any]]) -> str:
    """Serialize records to a portable CSV with JSON-encoded structured fields."""
    output = io.StringIO(newline="")
    fields = [
        "original_id",
        "content",
        "importance",
        "session_id",
        "persona_id",
        "memory_type",
        "status",
        "topics",
        "key_facts",
        "metadata",
        "source_messages",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for record in records:
        metadata = record.get("metadata") or {}
        writer.writerow(
            {
                "original_id": record.get("original_id", ""),
                "content": _escape_csv_formula(record.get("content", "")),
                "importance": record.get("importance", 0.5),
                "session_id": _escape_csv_formula(
                    record.get("session_id") or ""
                ),
                "persona_id": _escape_csv_formula(
                    record.get("persona_id") or ""
                ),
                "memory_type": _escape_csv_formula(
                    metadata.get("memory_type", "GENERAL")
                ),
                "status": _escape_csv_formula(
                    metadata.get("status", "active")
                ),
                "topics": json.dumps(metadata.get("topics", []), ensure_ascii=False),
                "key_facts": json.dumps(
                    metadata.get("key_facts", []), ensure_ascii=False
                ),
                "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
                "source_messages": json.dumps(
                    record.get("source_messages", []),
                    ensure_ascii=False,
                    default=str,
                ),
            }
        )
    return output.getvalue()


def _extract_json_entries(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        if _looks_like_message_list(payload):
            return [{"messages": payload}]
        return payload
    if not isinstance(payload, dict):
        raise ValueError("JSON 顶层必须是对象或数组")

    entries: list[Any] = []
    found_collection = False
    for key in _LONG_TERM_KEYS:
        if key not in payload:
            continue
        found_collection = True
        entries.extend(_as_entry_list(payload[key]))
    for key in _SHORT_TERM_KEYS:
        if key not in payload:
            continue
        found_collection = True
        entries.extend(_as_conversation_entries(payload[key]))
    if found_collection:
        return entries
    if "data" in payload and isinstance(payload["data"], (dict, list)):
        return _extract_json_entries(payload["data"])
    if not any(key in payload for key in (*_CONTENT_KEYS, *_SOURCE_KEYS)) and payload:
        mapped_entries: list[Any] = []
        for original_id, item in payload.items():
            if isinstance(item, dict):
                entry = dict(item)
                entry.setdefault("original_id", original_id)
                mapped_entries.append(entry)
            elif isinstance(item, str):
                mapped_entries.append(
                    {"original_id": original_id, "content": item}
                )
            else:
                mapped_entries = []
                break
        if mapped_entries:
            return mapped_entries
    return [payload]


def _as_entry_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        entries: list[Any] = []
        for original_id, item in value.items():
            if isinstance(item, dict):
                entry = dict(item)
                entry.setdefault("original_id", original_id)
                entries.append(entry)
            elif isinstance(item, str):
                entries.append({"original_id": original_id, "content": item})
        return entries
    return []


def _as_conversation_entries(value: Any) -> list[Any]:
    if isinstance(value, dict):
        entries: list[Any] = []
        for session_id, item in value.items():
            if isinstance(item, list):
                entries.append({"session_id": session_id, "messages": item})
            elif isinstance(item, dict):
                entry = dict(item)
                entry.setdefault("session_id", session_id)
                entries.append(entry)
        return entries
    if not isinstance(value, list):
        return _as_entry_list(value)
    if _looks_like_message_list(value):
        return [{"messages": value}]
    entries: list[Any] = []
    for item in value:
        if isinstance(item, list):
            entries.append({"messages": item})
        else:
            entries.append(item)
    return entries


def _looks_like_message_list(value: list[Any]) -> bool:
    if not value:
        return False
    return all(
        isinstance(item, dict)
        and any(key in item for key in ("role", "sender", "speaker"))
        and any(key in item for key in ("content", "text", "message", "value"))
        for item in value
    )


def _normalize_entry(raw: Any, index: int) -> ImportEntry:
    if isinstance(raw, str):
        raw = {"content": raw}
    if not isinstance(raw, dict):
        raise ValueError("记忆项必须是对象或文本")

    metadata = _parse_dict(raw.get("metadata"))
    content = _first_text(raw, _CONTENT_KEYS)
    session_id = _optional_text(
        raw.get("session_id", raw.get("session", metadata.get("session_id")))
    )
    persona_id = _optional_text(
        raw.get("persona_id", raw.get("persona", metadata.get("persona_id")))
    )
    source = _normalize_source(_first_value(raw, _SOURCE_KEYS), session_id)
    if not source:
        user_text = _first_text(raw, ("user", "human", "query", "input"))
        assistant_text = _first_text(
            raw, ("assistant", "ai", "bot", "response", "output")
        )
        if user_text and assistant_text:
            source = _normalize_source(
                [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text},
                ],
                session_id,
            )

    for key in ("topics", "key_facts", "participants"):
        value = _parse_list(raw.get(key))
        if value:
            metadata[key] = value
    if raw.get("memory_type") or raw.get("type"):
        metadata["memory_type"] = str(
            raw.get("memory_type") or raw.get("type")
        ).upper()
    if raw.get("status"):
        metadata["status"] = str(raw["status"]).lower()

    importance_raw = raw.get("importance", metadata.get("importance", 0.5))
    try:
        importance_number = float(importance_raw)
    except (TypeError, ValueError):
        importance_number = 0.5
    if 1.0 < importance_number <= 10.0:
        importance_number /= 10.0
    importance = clamp_float(importance_number, default=0.5)

    if not content and len(source) < 2:
        raise ValueError("缺少可导入的摘要文本或至少 2 条原始消息")

    metadata = dict(metadata)
    for key in ("has_source", "source_message_count", "atom_types", "previous_id"):
        metadata.pop(key, None)
    original_id = raw.get("original_id", raw.get("id"))
    if original_id is not None:
        metadata["imported_from_id"] = original_id

    return ImportEntry(
        source_index=index,
        content=content,
        importance=importance,
        session_id=session_id,
        persona_id=persona_id,
        metadata=metadata,
        source_messages=source,
    )


def _normalize_source(value: Any, session_id: str | None) -> list[dict[str, Any]]:
    parsed = _parse_json_value(value)
    if isinstance(parsed, dict):
        parsed = parsed.get("messages", parsed.get("conversation", []))
    if not isinstance(parsed, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, raw_message in enumerate(parsed):
        if isinstance(raw_message, str):
            raw_message = {"role": "user", "content": raw_message}
        if not isinstance(raw_message, dict):
            continue
        role = str(
            raw_message.get("role")
            or raw_message.get("sender")
            or raw_message.get("speaker")
            or "user"
        ).lower()
        if role in {"ai", "bot", "model"}:
            role = "assistant"
        elif role in {"human", "customer"}:
            role = "user"
        content = _first_text(raw_message, ("content", "text", "message", "value"))
        if not content:
            continue
        normalized.append(
            {
                "id": raw_message.get("id", index + 1),
                "session_id": raw_message.get("session_id") or session_id or "import",
                "role": role,
                "content": content,
                "sender_id": raw_message.get("sender_id") or role,
                "sender_name": raw_message.get("sender_name"),
                "group_id": raw_message.get("group_id"),
                "platform": raw_message.get("platform") or "import",
                "timestamp": raw_message.get("timestamp", 0.0),
                "metadata": raw_message.get("metadata") or {
                    "is_bot_message": role == "assistant"
                },
            }
        )
    return serialize_source_messages(normalized)


def _first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = _first_value(data, keys)
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _escape_csv_formula(value: Any) -> str:
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _unescape_csv_formula(value: str) -> str:
    if len(value) >= 2 and value[0] == "'" and value[1] in "=+-@\t\r":
        return value[1:]
    return value


def _unescape_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    scalar_fields = {
        "content",
        "session_id",
        "persona_id",
        "memory_type",
        "status",
    }
    return {
        key: _unescape_csv_formula(value)
        if key in scalar_fields and isinstance(value, str)
        else value
        for key, value in row.items()
    }


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _parse_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _parse_dict(value: Any) -> dict[str, Any]:
    parsed = _parse_json_value(value)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _parse_list(value: Any) -> list[Any]:
    parsed = _parse_json_value(value)
    return list(parsed) if isinstance(parsed, list) else []


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "ImportEntry",
    "MAX_IMPORT_ENTRIES",
    "TRANSFER_FORMAT",
    "TRANSFER_SCHEMA_VERSION",
    "memory_import_key",
    "parse_transfer_content",
    "serialize_transfer_csv",
    "serialize_transfer_json",
]

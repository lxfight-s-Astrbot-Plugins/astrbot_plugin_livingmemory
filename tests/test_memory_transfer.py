"""Tests for portable memory import/export formats."""

import json

from astrbot_plugin_livingmemory.core.memory_transfer import (
    memory_import_key,
    parse_transfer_content,
    serialize_transfer_csv,
    serialize_transfer_json,
)


def _record():
    return {
        "original_id": 7,
        "content": "User prefers concise answers",
        "importance": 0.8,
        "session_id": "session-1",
        "persona_id": "persona-1",
        "metadata": {
            "topics": ["preference"],
            "key_facts": ["concise answers"],
            "memory_type": "PREFERENCE",
        },
        "source_messages": [
            {
                "id": 1,
                "session_id": "session-1",
                "role": "user",
                "content": "Please keep answers concise",
                "sender_id": "user-1",
                "timestamp": 1.0,
                "metadata": {},
            }
        ],
    }


def test_native_json_round_trip_preserves_structured_fields():
    content = serialize_transfer_json([_record()], "2026-07-29T00:00:00+00:00")

    entries, errors = parse_transfer_content(content, "json")

    assert errors == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.content == "User prefers concise answers"
    assert entry.importance == 0.8
    assert entry.session_id == "session-1"
    assert entry.metadata["memory_type"] == "PREFERENCE"
    assert entry.metadata["imported_from_id"] == 7
    assert entry.source_messages[0]["content"] == "Please keep answers concise"


def test_csv_round_trip_preserves_metadata_and_source():
    content = serialize_transfer_csv([_record()])

    entries, errors = parse_transfer_content(content, "csv")

    assert errors == []
    entry = entries[0]
    assert entry.metadata["topics"] == ["preference"]
    assert entry.metadata["key_facts"] == ["concise answers"]
    assert entry.source_messages[0]["role"] == "user"


def test_csv_with_utf8_bom_is_supported():
    content = "\ufeff" + serialize_transfer_csv([_record()])

    entries, errors = parse_transfer_content(content, "csv")

    assert errors == []
    assert entries[0].content == "User prefers concise answers"


def test_csv_export_escapes_formulas_and_import_restores_text():
    record = _record()
    record["content"] = '=HYPERLINK("https://example.invalid")'
    record["session_id"] = "@external-session"

    content = serialize_transfer_csv([record])

    assert "'=HYPERLINK" in content
    assert "'@external-session" in content
    entries, errors = parse_transfer_content(content, "csv")
    assert errors == []
    assert entries[0].content == '=HYPERLINK("https://example.invalid")'
    assert entries[0].session_id == "@external-session"


def test_external_long_and_short_term_collections_are_normalized():
    payload = {
        "long_term_memories": [
            {
                "summary": "Deployment happens Friday",
                "importance": 8,
                "topics": ["release"],
            }
        ],
        "short_term_memories": [
            [
                {"sender": "human", "text": "The code is alpha"},
                {"sender": "ai", "text": "I will remember that"},
            ]
        ],
    }

    entries, errors = parse_transfer_content(json.dumps(payload), "json")

    assert errors == []
    assert entries[0].content == "Deployment happens Friday"
    assert entries[0].importance == 0.8
    assert entries[1].requires_summary is True
    assert [item["role"] for item in entries[1].source_messages] == [
        "user",
        "assistant",
    ]


def test_session_mapped_conversations_keep_external_session_id():
    payload = {
        "short_term_memories": {
            "external-session": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
            ]
        }
    }

    entries, errors = parse_transfer_content(json.dumps(payload), "json")

    assert errors == []
    assert entries[0].session_id == "external-session"
    assert entries[0].requires_summary is True


def test_memory_id_mapping_and_paired_turn_fields_are_supported():
    payload = {
        "memory-7": {"summary": "mapped summary"},
        "conversation-8": {"user": "question", "assistant": "answer"},
    }

    entries, errors = parse_transfer_content(json.dumps(payload), "json")

    assert errors == []
    assert entries[0].metadata["imported_from_id"] == "memory-7"
    assert entries[0].content == "mapped summary"
    assert entries[1].requires_summary is True
    assert [message["content"] for message in entries[1].source_messages] == [
        "question",
        "answer",
    ]


def test_nested_memory_id_mapping_preserves_original_id():
    payload = {"memories": {"memory-9": {"summary": "nested summary"}}}

    entries, errors = parse_transfer_content(json.dumps(payload), "json")

    assert errors == []
    assert entries[0].metadata["imported_from_id"] == "memory-9"


def test_invalid_entry_is_reported_without_rejecting_valid_items():
    payload = [{"summary": "valid"}, {"messages": [{"role": "user"}]}]

    entries, errors = parse_transfer_content(json.dumps(payload), "json")

    assert [entry.content for entry in entries] == ["valid"]
    assert errors == [
        {"index": 1, "error": "缺少可导入的摘要文本或至少 2 条原始消息"}
    ]


def test_duplicate_key_normalizes_whitespace_but_preserves_scope():
    assert memory_import_key("one   two", "s1", "p1") == memory_import_key(
        " one two ", "s1", "p1"
    )
    assert memory_import_key("one two", "s1", "p1") != memory_import_key(
        "one two", "s2", "p1"
    )

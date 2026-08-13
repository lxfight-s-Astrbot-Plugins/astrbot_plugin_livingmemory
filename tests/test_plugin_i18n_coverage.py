"""校验插件设置页 i18n 与配置 schema 保持同步。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "_conf_schema.json"
I18N_DIR = PROJECT_ROOT / ".astrbot-plugin" / "i18n"

EXPECTED_OPTION_LABELS = {
    "en-US": {
        "bot_language": {
            "zh": "Chinese",
            "en": "English",
            "ru": "Russian",
        },
        "recall_engine.injection_method": {
            "extra_user_content": "Extra User Content",
            "user_message_before": "Before User Message",
            "user_message_after": "After User Message",
            "fake_tool_call": "Fake Tool Call",
            "fake_tool_call_deepseek_v4": "Fake Tool Call (DeepSeek V4, deprecated)",
            "system_prompt": "System Prompt (deprecated)",
        },
        "recall_engine.memory_type_filter": {
            "all": "All Memories",
            "event_only": "Events and Facts",
        },
        "filtering_settings.memory_scope_mode": {
            "legacy": "Legacy",
            "session": "Per Session",
            "user": "Per User",
            "global": "Global",
        },
        "memory_consolidation.trigger": {
            "daily": "Daily",
            "reflection": "On Reflection",
        },
        "memory_consolidation.granularity": {
            "session": "Same Session",
            "semantic": "Semantic Clustering",
        },
        "memory_consolidation.keep_original": {
            "archive": "Archive",
            "delete": "Delete",
        },
    },
    "ru-RU": {
        "bot_language": {
            "zh": "Китайский",
            "en": "Английский",
            "ru": "Русский",
        },
        "recall_engine.injection_method": {
            "extra_user_content": "Дополнительный контент пользователя",
            "user_message_before": "Перед сообщением пользователя",
            "user_message_after": "После сообщения пользователя",
            "fake_tool_call": "Фейковый вызов инструмента",
            "fake_tool_call_deepseek_v4": (
                "Фейковый вызов инструмента (DeepSeek V4, устарело)"
            ),
            "system_prompt": "Системная инструкция (устарело)",
        },
        "recall_engine.memory_type_filter": {
            "all": "Все воспоминания",
            "event_only": "События и факты",
        },
        "filtering_settings.memory_scope_mode": {
            "legacy": "Legacy",
            "session": "По сессии",
            "user": "По пользователю",
            "global": "Глобально",
        },
        "memory_consolidation.trigger": {
            "daily": "Ежедневно",
            "reflection": "При рефлексии",
        },
        "memory_consolidation.granularity": {
            "session": "По сессии",
            "semantic": "Семантическая кластеризация",
        },
        "memory_consolidation.keep_original": {
            "archive": "Архивировать",
            "delete": "Удалить",
        },
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_schema_items(
    schema: dict[str, Any], prefix: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for key, meta in schema.items():
        if not isinstance(meta, dict) or meta.get("invisible"):
            continue

        path = f"{prefix}.{key}" if prefix else key
        items.append((path, meta))

        if meta.get("type") == "object" and isinstance(meta.get("items"), dict):
            items.extend(_iter_schema_items(meta["items"], path))

    return items


def _get_path(source: dict[str, Any], path: str) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def test_plugin_i18n_covers_visible_schema_text() -> None:
    schema_items = _iter_schema_items(_load_json(SCHEMA_PATH))
    locale_paths = sorted(I18N_DIR.glob("*.json"))

    assert locale_paths, "缺少插件 i18n locale 文件"

    for locale_path in locale_paths:
        locale_config = _load_json(locale_path).get("config", {})
        missing: list[str] = []

        for config_path, meta in schema_items:
            locale_item = _get_path(locale_config, config_path)
            for attr in ("description", "hint"):
                if not meta.get(attr):
                    continue
                if not isinstance(locale_item, dict) or not locale_item.get(attr):
                    missing.append(f"{config_path}.{attr}")

        assert not missing, f"{locale_path.name} 缺少配置文案覆盖: {missing}"


def test_plugin_i18n_covers_option_labels() -> None:
    schema_items = _iter_schema_items(_load_json(SCHEMA_PATH))
    option_items = [
        (config_path, meta)
        for config_path, meta in schema_items
        if isinstance(meta.get("options"), list)
    ]

    assert option_items, "schema 中没有发现 options 配置项"

    for locale_path in sorted(I18N_DIR.glob("*.json")):
        locale_name = locale_path.stem
        locale_config = _load_json(locale_path).get("config", {})
        bad_labels: list[str] = []

        for config_path, meta in option_items:
            locale_item = _get_path(locale_config, config_path)
            labels = locale_item.get("labels") if isinstance(locale_item, dict) else None
            if not isinstance(labels, list) or len(labels) != len(meta["options"]):
                bad_labels.append(config_path)
                continue

            if any(not isinstance(label, str) or not label.strip() for label in labels):
                bad_labels.append(f"{config_path} has empty labels")
                continue

            expected_labels = EXPECTED_OPTION_LABELS.get(locale_name, {}).get(
                config_path
            )
            if not expected_labels:
                bad_labels.append(f"{config_path} missing expected label mapping")
                continue

            for option, label in zip(meta["options"], labels, strict=True):
                if expected_labels.get(option) != label:
                    bad_labels.append(f"{config_path}.{option}")

        assert not bad_labels, f"{locale_path.name} 缺少等长 labels: {bad_labels}"

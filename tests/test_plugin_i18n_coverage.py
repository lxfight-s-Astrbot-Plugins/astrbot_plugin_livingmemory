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
            "ja": "Japanese",
        },
        "provider_settings.llm_stream_mode": {
            "auto": "Auto (Recommended)",
            "always": "Always Stream",
            "never": "Never Stream",
        },
        "recall_engine.injection_method": {
            "extra_user_content": "Extra User Content",
        },
        "recall_engine.fusion_mode": {
            "score": "Normalized Score",
            "rrf": "Reciprocal Rank Fusion (RRF)",
        },
        "recall_engine.memory_type_filter": {
            "all": "All Memories",
            "event_only": "Events and Facts",
        },
        "recall_engine.tokenizer": {
            "jieba": "jieba word segmentation",
            "builtin": "Built-in tokenizer (low memory)",
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
            "ja": "Японский",
        },
        "provider_settings.llm_stream_mode": {
            "auto": "Автоматически (рекомендуется)",
            "always": "Всегда потоково",
            "never": "Всегда без потока",
        },
        "recall_engine.injection_method": {
            "extra_user_content": "Дополнительный контент пользователя",
        },
        "recall_engine.fusion_mode": {
            "score": "Нормализованная оценка",
            "rrf": "Обратное ранговое объединение (RRF)",
        },
        "recall_engine.memory_type_filter": {
            "all": "Все воспоминания",
            "event_only": "События и факты",
        },
        "recall_engine.tokenizer": {
            "jieba": "Сегментация jieba",
            "builtin": "Встроенный токенизатор (меньше памяти)",
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
    "zh-CN": {
        "bot_language": {
            "zh": "中文",
            "en": "English",
            "ru": "Русский",
            "ja": "日本語",
        },
        "provider_settings.llm_stream_mode": {
            "auto": "自动（推荐）",
            "always": "始终流式",
            "never": "始终非流式",
        },
        "recall_engine.injection_method": {
            "extra_user_content": "追加到用户消息末尾",
        },
        "recall_engine.fusion_mode": {
            "score": "归一化分数",
            "rrf": "倒数排名融合（RRF）",
        },
        "recall_engine.memory_type_filter": {
            "all": "全部记忆",
            "event_only": "事件与事实",
        },
        "recall_engine.tokenizer": {
            "jieba": "jieba 词级分词",
            "builtin": "内置分词（省内存）",
        },
        "filtering_settings.memory_scope_mode": {
            "legacy": "Legacy（旧版）",
            "session": "按会话隔离",
            "user": "按用户共享",
            "global": "全局共享",
        },
        "memory_consolidation.trigger": {
            "daily": "每日定时",
            "reflection": "反思时",
        },
        "memory_consolidation.granularity": {
            "session": "同一会话",
            "semantic": "语义聚类",
        },
        "memory_consolidation.keep_original": {
            "archive": "归档保留",
            "delete": "直接删除",
        },
    },
    "ja-JP": {
        "bot_language": {
            "zh": "中国語",
            "en": "English",
            "ru": "Русский",
            "ja": "日本語",
        },
        "provider_settings.llm_stream_mode": {
            "auto": "自動（推奨）",
            "always": "常にストリーミング",
            "never": "常に非ストリーミング",
        },
        "recall_engine.injection_method": {
            "extra_user_content": "ユーザーメッセージ末尾",
        },
        "recall_engine.fusion_mode": {
            "score": "正規化スコア",
            "rrf": "逆順位融合（RRF）",
        },
        "recall_engine.memory_type_filter": {
            "all": "すべての記憶",
            "event_only": "出来事と事実",
        },
        "recall_engine.tokenizer": {
            "jieba": "jieba 単語分割",
            "builtin": "組み込みトークナイザー（省メモリ）",
        },
        "filtering_settings.memory_scope_mode": {
            "legacy": "Legacy（旧版）",
            "session": "セッション単位",
            "user": "ユーザー単位",
            "global": "グローバル",
        },
        "memory_consolidation.trigger": {
            "daily": "毎日定時",
            "reflection": "リフレクション時",
        },
        "memory_consolidation.granularity": {
            "session": "同一セッション",
            "semantic": "意味的クラスタリング",
        },
        "memory_consolidation.keep_original": {
            "archive": "アーカイブして保持",
            "delete": "直接削除",
        },
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_schema_items(
    schema: dict[str, Any], prefix: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    """Yield every schema item that is rendered somewhere in the UI.

    Dashboard-rendered groups are marked ``invisible`` on the host config page,
    but their fields are still served by ``page/config`` and rendered by the
    dashboard Settings page. Recurse into groups regardless of visibility and
    only skip the leaves that are explicitly hidden.
    """
    items: list[tuple[str, dict[str, Any]]] = []
    for key, meta in schema.items():
        if not isinstance(meta, dict):
            continue

        path = f"{prefix}.{key}" if prefix else key
        if not meta.get("invisible"):
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

    schema_paths = {path for path, _ in schema_items}
    # 守卫必须真正覆盖 Dashboard 渲染的隐藏分组 + 宿主配置页字段。
    assert len(schema_paths) >= 100, f"schema 覆盖疑似回退: {len(schema_paths)} 项"
    for required in (
        "provider_settings.embedding_provider_id",
        "access_control.identity_aliases",
        "reflection_engine.max_segments_per_run",
        "filtering_settings.memory_scope_mode",
    ):
        assert required in schema_paths, f"守卫未覆盖 {required}"

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
            labels = (
                locale_item.get("labels") if isinstance(locale_item, dict) else None
            )
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

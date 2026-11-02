"""Expose advanced plugin configuration through the dashboard settings page.

The AstrBot plugin configuration page keeps only providers and language; the
remaining sections are marked ``invisible`` in ``_conf_schema.json`` and are
rendered by this handler for the plugin's own Pages UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quart import request

from astrbot.api import logger

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PLUGIN_ROOT / "_conf_schema.json"
LOCALE_DIR = PLUGIN_ROOT / ".astrbot-plugin" / "i18n"
LANG_TO_LOCALE = {"zh": "zh-CN", "en": "en-US", "ru": "ru-RU", "ja": "ja-JP"}

# Providers and reply language stay on the host configuration page.
HOST_CONFIG_GROUPS = {"provider_settings", "bot_language"}

_SLIDER_KEYS = ("min", "max", "step")


def _get_path(source: Any, path: str) -> Any:
    current = source
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class SettingsHandler:
    """Read and update the hidden advanced configuration sections."""

    def __init__(self, utils, plugin) -> None:
        self.utils = utils
        self.plugin = plugin
        self._schema: dict[str, Any] | None = None
        self._locales: dict[str, dict[str, Any]] = {}

    # ---- schema / locale loading ----

    def _adapter_options(self):
        """List actual adapter instances, retaining saved IDs for offline adapters."""
        options = {
            item["id"]: f"{item['id']} ({item['type']})"
            for item in self.plugin.context.get_config().get("platform", [])
            if item.get("id")
        }
        options.update(
            {
                item.meta().id: f"{item.meta().id} ({item.meta().name})"
                for item in self.plugin.context.platform_manager.get_insts()
            }
        )
        for adapter_id in (
            self.plugin.config_manager.get("filtering_settings.shared_adapter_ids", [])
            or []
        ):
            options.setdefault(adapter_id, adapter_id)
        return options

    def _load_schema(self) -> dict[str, Any]:
        if self._schema is None:
            try:
                self._schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                logger.error("读取配置 schema 失败: %s", exc, exc_info=True)
                self._schema = {}
        return self._schema

    def _load_locale(self, lang: str) -> dict[str, Any]:
        locale_name = LANG_TO_LOCALE.get(str(lang).lower(), "zh-CN")
        if locale_name not in self._locales:
            path = LOCALE_DIR / f"{locale_name}.json"
            try:
                self._locales[locale_name] = json.loads(
                    path.read_text(encoding="utf-8-sig")
                )
            except Exception:
                self._locales[locale_name] = {}
        return self._locales[locale_name]

    @staticmethod
    def _localized(locale_data: dict[str, Any], path: str, attr: str) -> str | None:
        value = _get_path((locale_data or {}).get("config", {}), path)
        if isinstance(value, dict):
            text = value.get(attr)
            if isinstance(text, str) and text.strip():
                return text
        return None

    @staticmethod
    def _localized_labels(locale_data: dict[str, Any], path: str) -> list[str] | None:
        value = _get_path((locale_data or {}).get("config", {}), path)
        if isinstance(value, dict):
            labels = value.get("labels")
            if isinstance(labels, list) and all(
                isinstance(label, str) for label in labels
            ):
                return labels
        return None

    # ---- field enumeration ----

    def _groups(self) -> list[tuple[str, dict[str, Any]]]:
        groups = []
        for key, meta in self._load_schema().items():
            if key in HOST_CONFIG_GROUPS or not isinstance(meta, dict):
                continue
            if meta.get("invisible") and isinstance(meta.get("items"), dict):
                groups.append((key, meta))
        return groups

    def _flatten_fields(
        self,
        group: str,
        prefix: str,
        items: dict[str, Any],
        locale_data: dict[str, Any],
        config_manager,
    ) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        for key, meta in items.items():
            if not isinstance(meta, dict) or meta.get("invisible"):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if meta.get("type") == "object" and isinstance(meta.get("items"), dict):
                fields.extend(
                    self._flatten_fields(
                        group, path, meta["items"], locale_data, config_manager
                    )
                )
                continue

            fallback_description = str(meta.get("description") or key)
            fallback_hint = str(meta.get("hint") or "")
            description = (
                self._localized(locale_data, path, "description")
                or fallback_description
            )
            hint = self._localized(locale_data, path, "hint") or fallback_hint
            options = meta.get("options")
            labels = (
                self._localized_labels(locale_data, path)
                if isinstance(options, list)
                else None
            )
            slider = meta.get("slider") if isinstance(meta.get("slider"), dict) else {}
            field: dict[str, Any] = {
                "path": f"{group}.{path}",
                "name": path,
                "label": description,
                "hint": hint,
                "type": str(meta.get("type") or "string"),
                "default": meta.get("default"),
                "value": config_manager.get(f"{group}.{path}", meta.get("default")),
            }
            if isinstance(options, list):
                field["options"] = options
                field["option_labels"] = labels or options
            for key_name in _SLIDER_KEYS:
                if key_name in slider:
                    field[key_name] = slider[key_name]
            fields.append(field)
        return fields

    async def get_config(self) -> dict[str, Any]:
        """Return advanced sections with metadata, current values and localization."""
        lang = str(request.args.get("lang", "zh") or "zh")
        locale_data = self._load_locale(lang)
        config_manager = getattr(self.plugin, "config_manager", None)
        if config_manager is None:
            return self.utils.error("配置管理器未初始化")

        sections = []
        adapter_options = self._adapter_options()
        for group, meta in self._groups():
            group_title = self._localized(locale_data, group, "description") or str(
                meta.get("description") or group
            )
            fields = self._flatten_fields(
                group, "", meta.get("items") or {}, locale_data, config_manager
            )
            for field in fields:
                if field["path"] == "filtering_settings.shared_adapter_ids":
                    field.update(
                        type="adapter-list",
                        options=list(adapter_options),
                        option_labels=list(adapter_options.values()),
                    )
            if fields:
                sections.append(
                    {
                        "key": group,
                        "title": group_title,
                        "fields": fields,
                    }
                )
        initializer = getattr(self.plugin, "initializer", None)
        engine = (
            initializer.memory_engine
            if initializer is not None and initializer.is_initialized
            else None
        )
        ownership = await engine.adapter_migrator.survey() if engine is not None else []
        return self.utils.ok({"sections": sections, "adapter_ownership": ownership})

    async def assign_adapter(self):
        """Assign unowned legacy records selected by their original memory scope."""
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self.utils.error("Invalid assignment payload")
        adapter_id = payload.get("adapter_id")
        if (
            not isinstance(adapter_id, str)
            or not adapter_id
            or adapter_id not in self._adapter_options()
        ):
            return self.utils.error("Unknown adapter instance")
        if "session_id" not in payload:
            return self.utils.error("Missing legacy memory scope")
        if any(
            value is not None and not isinstance(value, str)
            for value in (payload["session_id"], payload.get("source_adapter_id"))
        ):
            return self.utils.error("Invalid legacy memory scope")
        ready, message = await self.plugin._ensure_plugin_ready()
        if not ready:
            return self.utils.error(message)
        engine = self.plugin.initializer.memory_engine
        async with engine._db_write_lock:
            cursor = await engine.db_connection.execute(
                "SELECT id FROM documents WHERE json_extract(metadata, '$.adapter_id') IS ? "
                "AND (? IS NOT NULL OR json_extract(metadata, '$.session_id') IS ?)",
                (
                    payload.get("source_adapter_id"),
                    payload.get("source_adapter_id"),
                    payload["session_id"],
                ),
            )
            assignments = {int(row[0]): adapter_id for row in await cursor.fetchall()}
            result = await engine.adapter_migrator.assign(assignments)
        engine._invalidate_search_cache()
        return self.utils.ok(result)

    def _allowed_paths(self) -> set[str]:
        allowed: set[str] = set()

        def walk(prefix: str, items: dict[str, Any]) -> None:
            for key, meta in items.items():
                if not isinstance(meta, dict) or meta.get("invisible"):
                    continue
                path = f"{prefix}.{key}" if prefix else key
                if meta.get("type") == "object" and isinstance(meta.get("items"), dict):
                    walk(path, meta["items"])
                else:
                    allowed.add(path)

        for group, meta in self._groups():
            walk(group, meta.get("items") or {})
        return allowed

    async def update_config(self) -> dict[str, Any]:
        """Persist flat dotted-path updates to the host plugin config."""
        payload = await request.get_json(silent=True) or {}
        values = payload.get("values")
        if not isinstance(values, dict) or not values:
            return self.utils.error("没有需要保存的配置项")
        unknown = sorted(set(values) - self._allowed_paths())
        if unknown:
            return self.utils.error(f"不支持的配置项: {', '.join(unknown)}")

        config_manager = getattr(self.plugin, "config_manager", None)
        if config_manager is None:
            return self.utils.error("配置管理器未初始化")
        try:
            updated = config_manager.apply_updates(values)
        except Exception as exc:
            logger.warning("保存插件配置失败: %s", exc, exc_info=True)
            return self.utils.error(f"保存失败: {exc}")

        return self.utils.ok(
            {
                "message": "saved",
                "values": {path: _get_path(updated, path) for path in sorted(values)},
            }
        )

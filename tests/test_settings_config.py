"""Advanced configuration editing through the dashboard settings API."""

from types import SimpleNamespace

import pytest
from quart import Quart

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.page_api_modules.settings_handler import (
    SettingsHandler,
)
from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils


class _WritableConfig(dict):
    """Minimal AstrBotConfig stand-in that records save_config calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self):
        self.saved += 1


def _handler(config_manager: ConfigManager) -> SettingsHandler:
    plugin = SimpleNamespace(config_manager=config_manager)
    return SettingsHandler(PageApiUtils(), plugin)


def test_apply_updates_persists_normalized_values() -> None:
    raw = _WritableConfig({"recall_engine": {"top_k": 5}, "memory_dedup": {}})
    manager = ConfigManager(raw)

    updated = manager.apply_updates(
        {"recall_engine.top_k": 999, "memory_dedup.enabled": False}
    )

    assert raw.saved == 1
    assert updated["recall_engine"]["top_k"] == 50  # clamped to the allowed maximum
    assert updated["memory_dedup"]["enabled"] is False
    assert manager.get("recall_engine.top_k") == 50
    assert manager.get("memory_dedup.enabled") is False


def test_apply_updates_requires_writable_config() -> None:
    manager = ConfigManager({"recall_engine": {"top_k": 5}})

    with pytest.raises(RuntimeError):
        manager.apply_updates({"recall_engine.top_k": 8})


def test_settings_groups_are_only_the_hidden_advanced_sections() -> None:
    handler = _handler(ConfigManager({}))
    groups = [key for key, _ in handler._groups()]

    assert "provider_settings" not in groups
    assert "bot_language" not in groups
    assert {"recall_engine", "memory_dedup", "memory_consolidation"} <= set(groups)


def test_settings_allowed_paths_exclude_host_config_and_include_new_options() -> None:
    allowed = _handler(ConfigManager({}))._allowed_paths()

    assert "memory_dedup.enabled" in allowed
    assert "memory_dedup.similarity_threshold" in allowed
    assert "recall_engine.top_k" in allowed
    assert "provider_settings.embedding_provider_id" not in allowed
    assert "bot_language" not in allowed


def test_settings_fields_are_localized_and_carry_values() -> None:
    handler = _handler(ConfigManager({}))
    locale_data = handler._load_locale("zh")
    sections = {}
    for group, meta in handler._groups():
        fields = handler._flatten_fields(
            group, "", meta.get("items") or {}, locale_data, handler.plugin.config_manager
        )
        sections[group] = fields

    recall = {field["path"]: field for field in sections["recall_engine"]}
    top_k = recall["recall_engine.top_k"]
    assert top_k["value"] == 5
    assert top_k["type"] == "int"
    assert top_k["min"] == 0 and top_k["max"] == 50
    assert top_k["label"] and top_k["label"] != "top_k"

    dedup = {field["path"]: field for field in sections["memory_dedup"]}
    assert dedup["memory_dedup.enabled"]["value"] is True
    assert dedup["memory_dedup.similarity_threshold"]["value"] == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_settings_routes_read_and_update_config() -> None:
    raw = _WritableConfig({})
    manager = ConfigManager(raw)
    handler = _handler(manager)

    app = Quart(__name__)

    @app.get("/config")
    async def get_config():
        return await handler.get_config()

    @app.post("/config/update")
    async def update_config():
        return await handler.update_config()

    client = app.test_client()
    response = await client.get("/config?lang=zh")
    payload = await response.get_json()
    assert payload["status"] == "ok"
    sections = payload["data"]["sections"]
    assert {section["key"] for section in sections} >= {"recall_engine", "memory_dedup"}
    for section in sections:
        for field in section["fields"]:
            assert field["label"]
            assert field["path"].startswith(section["key"] + ".")

    response = await client.post(
        "/config/update", json={"values": {"recall_engine.top_k": 7}}
    )
    payload = await response.get_json()
    assert payload["status"] == "ok"
    assert payload["data"]["values"]["recall_engine.top_k"] == 7
    assert raw.saved == 1
    assert manager.get("recall_engine.top_k") == 7

    response = await client.post(
        "/config/update",
        json={"values": {"provider_settings.embedding_provider_id": "x"}},
    )
    payload = await response.get_json()
    assert payload["status"] == "error"

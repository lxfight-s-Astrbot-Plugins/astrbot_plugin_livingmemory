"""Advanced configuration editing through the dashboard settings API."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import json

import aiosqlite

import pytest
from quart import Quart

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.page_api_modules.settings_handler import (
    SettingsHandler,
)
from astrbot_plugin_livingmemory.core.page_api_modules.utils import PageApiUtils
from astrbot_plugin_livingmemory.core.managers.adapter_migrator import AdapterMigrator


class _WritableConfig(dict):
    """Minimal AstrBotConfig stand-in that records save_config calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self):
        self.saved += 1


def _handler(config_manager: ConfigManager) -> SettingsHandler:
    plugin = SimpleNamespace(
        config_manager=config_manager,
        context=SimpleNamespace(
            get_config=lambda: {"platform": []},
            platform_manager=SimpleNamespace(get_insts=lambda: []),
        ),
    )
    return SettingsHandler(PageApiUtils(), plugin)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_adapter_id, updated_id", [(None, 1), ("qq-a", 2)])
async def test_adapter_assignment_moves_legacy_ownership_without_changing_scope(
    tmp_path, source_adapter_id, updated_id
):
    path = tmp_path / "legacy.db"
    db = await aiosqlite.connect(path)
    await db.executescript("""
        CREATE TABLE documents(id INTEGER PRIMARY KEY, text TEXT, metadata TEXT);
        CREATE TABLE migration_status(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        INSERT INTO documents VALUES(1, 'legacy', '{"session_id":"livingmemory:global"}');
        INSERT INTO documents VALUES(2, 'known', '{"session_id":"qq-a:FriendMessage:123","adapter_id":"qq-a"}');
    """)
    await db.commit()
    meta = SimpleNamespace(id="wechat-a", name="wechat")
    host = SimpleNamespace(meta=lambda: meta)
    engine = SimpleNamespace(
        db_connection=db,
        _db_write_lock=asyncio.Lock(),
        adapter_migrator=AdapterMigrator(str(path)),
        _invalidate_search_cache=lambda: None,
    )
    plugin = SimpleNamespace(
        config_manager=ConfigManager({}),
        initializer=SimpleNamespace(memory_engine=engine),
        context=SimpleNamespace(
            get_config=lambda: {"platform": []},
            platform_manager=SimpleNamespace(get_insts=lambda: [host]),
        ),
        _ensure_plugin_ready=AsyncMock(return_value=(True, "")),
    )
    handler = SettingsHandler(PageApiUtils(), plugin)
    try:
        app = Quart(__name__)
        async with app.test_request_context(
            "/",
            method="POST",
            json={
                "adapter_id": "wechat-a",
                "source_adapter_id": source_adapter_id,
                "session_id": "livingmemory:global"
                if source_adapter_id is None
                else None,
            },
        ):
            result = await handler.assign_adapter()
        assert result["data"]["updated"] == 1
        assert result["data"]["backup_path"]
        rows = await (
            await db.execute("SELECT metadata FROM documents ORDER BY id")
        ).fetchall()
        selected = json.loads(rows[updated_id - 1][0])
        assert selected["adapter_id"] == "wechat-a"
        assert selected["session_id"] == (
            "livingmemory:global" if updated_id == 1 else "qq-a:FriendMessage:123"
        )
        other = json.loads(rows[1 if updated_id == 1 else 0][0])
        assert other.get("adapter_id") == ("qq-a" if updated_id == 1 else None)
    finally:
        await db.close()


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
            group,
            "",
            meta.get("items") or {},
            locale_data,
            handler.plugin.config_manager,
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


@pytest.mark.asyncio
async def test_adapter_choices_keep_offline_instances_of_the_same_type():
    handler = _handler(ConfigManager({}))
    handler.plugin.context.get_config = lambda: {
        "platform": [
            {"id": "qq-a", "type": "aiocqhttp"},
            {"id": "qq-b", "type": "aiocqhttp", "enable": False},
        ]
    }
    async with Quart(__name__).test_request_context("/"):
        response = await handler.get_config()
    field = next(
        field
        for section in response["data"]["sections"]
        for field in section["fields"]
        if field["path"] == "filtering_settings.shared_adapter_ids"
    )
    assert field["options"] == ["qq-a", "qq-b"]
    assert field["value"] is None

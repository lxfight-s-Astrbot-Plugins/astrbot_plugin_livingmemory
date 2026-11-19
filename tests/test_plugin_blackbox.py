"""Black-box plugin tests: boot the real plugin against a stub host.

The plugin is constructed through its public constructor with the only test
double being the AstrBot host (``Context``), and everything below it — config,
initializer, SQLite/FAISS storage, conversation store, commands, hooks — is the
real implementation. Assertions look at what a user or the host can observe:
command replies, injected prompt content, registered tools and stored memories.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

import astrbot_plugin_livingmemory.main as plugin_main
from astrbot.api.platform import MessageType
from astrbot.core.provider.entities import LLMResponse, ProviderMeta
from astrbot.core.provider.provider import Provider

from tests.integration.test_real_db_end_to_end import (
    _DeterministicEmbeddingProvider,
    _DeterministicLLMProvider,
)


class _FakeChatProvider(Provider):
    """A real ``Provider`` subclass so the initializer accepts it.

    Chat completions are delegated to the deterministic fixture provider, which
    returns a well-formed summary payload; no network access happens.
    """

    def __init__(self) -> None:
        super().__init__({"id": "llm", "type": "fake_chat"}, {})
        self._inner = _DeterministicLLMProvider()

    def meta(self) -> ProviderMeta:
        return ProviderMeta(id="llm", model="fake", type="fake_chat")

    def get_current_key(self) -> str:
        return "test-key"

    def set_key(self, key: str) -> None:
        self.provider_config["key"] = key

    async def get_models(self) -> list[str]:
        return ["fake"]

    def get_model(self) -> str:
        return "fake"

    async def text_chat(self, *args, **kwargs) -> LLMResponse:
        return await self._inner.text_chat(*args, **kwargs)

CONFIG = {
    "provider_settings": {
        "embedding_provider_id": "emb",
        "llm_provider_id": "llm",
    },
    "recall_engine": {"top_k": 5, "min_similarity_for_retrieval": 0.0},
    "filtering_settings": {"memory_scope_mode": "global"},
    "reflection_engine": {"summary_trigger_rounds": 100},
    "graph_memory": {"enabled": False},
    "agent_tools": {"enable_recall_tool": True, "enable_memorize_tool": True},
}


class _HostContext:
    """The AstrBot host surface the plugin touches, recording registrations."""

    def __init__(self, embedding, llm) -> None:
        self.platform_manager = SimpleNamespace(get_insts=lambda: [])
        self.web_apis: dict[str, tuple] = {}
        self.llm_tools: list = []
        self.embedding = embedding
        self.llm = llm

    def get_config(self) -> dict:
        return {"platform": []}

    def register_web_api(self, path, handler, methods, description="") -> None:
        self.web_apis[path] = (handler, methods)

    def add_llm_tools(self, *tools) -> None:
        self.llm_tools.extend(tools)

    def get_provider_by_id(self, provider_id):
        return {"emb": self.embedding, "llm": self.llm}.get(provider_id)

    def get_all_embedding_providers(self) -> list:
        return [self.embedding]

    def get_all_providers(self) -> list:
        return [self.llm]

    async def get_using_provider_async(self, umo=None):
        return self.llm


class _Event:
    def __init__(self, umo: str = "host:FriendMessage:user-1", group_id: str = "") -> None:
        self.unified_msg_origin = umo
        self.group_id = group_id
        self.sender_id = "user-1"
        self.sender_name = "User"
        self.message_str = "what tea do I like"

    def plain_result(self, message):
        return message

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return []

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def get_self_id(self):
        return "bot-1"

    def get_platform_name(self):
        return "host"

    def get_platform_id(self):
        return "host-instance"

    def get_group_id(self):
        return self.group_id

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE if self.group_id else MessageType.FRIEND_MESSAGE

    def get_extra(self, key, default=None):
        return default


async def _boot_plugin(monkeypatch, tmp_path, config: dict | None = None):
    """Construct the real plugin and wait for its background initialization."""
    monkeypatch.setattr(
        plugin_main.StarTools, "get_data_dir", lambda _name: str(tmp_path)
    )
    host = _HostContext(_DeterministicEmbeddingProvider(), _FakeChatProvider())
    plugin = plugin_main.LivingMemoryPlugin(host, dict(CONFIG if config is None else config))

    for _ in range(400):
        if plugin.initializer.is_initialized or plugin.initializer.is_failed:
            break
        await asyncio.sleep(0.05)
    assert plugin.initializer.is_initialized, plugin.initializer.error_message
    return plugin, host


async def _collect(agen) -> list[str]:
    return [item async for item in agen]


@pytest_asyncio.fixture
async def plugin(monkeypatch, tmp_path):
    plugin, host = await _boot_plugin(monkeypatch, tmp_path)
    try:
        yield plugin, host
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_boot_registers_page_routes_and_agent_tools(plugin):
    _, host = plugin

    assert any(path.startswith("/astrbot_plugin_livingmemory/page/") for path in host.web_apis)
    assert [tool.name for tool in host.llm_tools] == [
        "recall_long_term_memory",
        "memorize_long_term_memory",
    ]


@pytest.mark.asyncio
async def test_user_can_store_and_search_a_memory(plugin):
    plugin, _ = plugin
    event = _Event()

    memorize = next(tool for tool in plugin.context.llm_tools if tool.name == "memorize_long_term_memory")
    result = await memorize.call(
        SimpleNamespace(context=SimpleNamespace(event=event)),
        memory="User prefers oat milk lattes",
        importance=0.8,
    )
    assert '"memorized":true' in result

    reply = (await _collect(plugin.search(event, "oat milk")))[-1]
    assert "oat milk lattes" in reply


@pytest.mark.asyncio
async def test_status_reports_stored_memory_count(plugin):
    plugin, _ = plugin
    event = _Event()
    await plugin.initializer.memory_engine.add_memory(
        "User keeps a spare key with Xiao Ning",
        session_id="livingmemory:global",
        persona_id=None,
    )

    reply = (await _collect(plugin.status(event)))[-1]
    assert "1" in reply


@pytest.mark.asyncio
async def test_blacklisted_group_gets_no_injected_memories(monkeypatch, tmp_path):
    config = dict(CONFIG)
    config["filtering_settings"] = {
        "memory_scope_mode": "global",
        "group_blacklist": ["blocked-group"],
    }
    plugin, _ = await _boot_plugin(monkeypatch, tmp_path, config)
    try:
        await plugin.initializer.memory_engine.add_memory(
            "User likes jasmine tea",
            session_id="livingmemory:global",
            persona_id=None,
        )
        event = _Event(umo="host:GroupMessage:blocked-group", group_id="blocked-group")
        request = SimpleNamespace(prompt="what tea do I like", extra_user_content_parts=[])

        await plugin.handle_memory_recall(event, request)

        assert request.extra_user_content_parts == []
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_recall_injects_matching_memory_for_normal_group(plugin):
    plugin, _ = plugin
    await plugin.initializer.memory_engine.add_memory(
        "User likes jasmine tea",
        session_id="livingmemory:global",
        persona_id=None,
    )
    event = _Event(umo="host:GroupMessage:normal-group", group_id="normal-group")
    request = SimpleNamespace(prompt="what tea do I like", extra_user_content_parts=[])

    await plugin.handle_memory_recall(event, request)

    injected = "".join(part.text for part in request.extra_user_content_parts)
    assert "jasmine tea" in injected


@pytest.mark.asyncio
async def test_forget_removes_the_memory_from_search(plugin):
    plugin, _ = plugin
    event = _Event()
    memory_id = await plugin.initializer.memory_engine.add_memory(
        "User has a cat named Miko",
        session_id="livingmemory:global",
        persona_id=None,
    )

    await _collect(plugin.forget(event, memory_id))

    reply = (await _collect(plugin.search(event, "Miko")))[-1]
    assert "未找到" in reply


@pytest.mark.asyncio
async def test_commands_refuse_to_run_after_terminate(monkeypatch, tmp_path):
    plugin, _ = await _boot_plugin(monkeypatch, tmp_path)
    await plugin.terminate()

    reply = (await _collect(plugin.status(_Event())))[-1]
    assert "初始化" in reply or "未就绪" in reply


TOOL_SWITCH_CASES = [
    ({}, ["recall_long_term_memory"]),
    (
        {"enable_recall_tool": True, "enable_agentic_recall_beta": True},
        ["recall_long_term_memory", "read_memory_evidence"],
    ),
    (
        {"enable_recall_tool": True, "enable_memorize_tool": True},
        ["recall_long_term_memory", "memorize_long_term_memory"],
    ),
    (
        {"enable_recall_tool": True, "enable_forget_tool": True},
        ["recall_long_term_memory", "forget_long_term_memory"],
    ),
    (
        {"enable_recall_tool": False, "enable_agentic_recall_beta": True},
        [],
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("agent_tools", "expected"), TOOL_SWITCH_CASES)
async def test_agent_tool_registration_follows_config(
    monkeypatch, tmp_path, agent_tools, expected
):
    config = dict(CONFIG)
    config["agent_tools"] = agent_tools
    plugin, host = await _boot_plugin(monkeypatch, tmp_path, config)
    try:
        assert [tool.name for tool in host.llm_tools] == expected
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_help_command_lists_user_facing_commands(plugin):
    plugin, _ = plugin

    reply = (await _collect(plugin.help(_Event())))[-1]

    for command in ("status", "search", "forget", "summarize"):
        assert f"/lmem {command}" in reply


@pytest.mark.asyncio
async def test_diag_command_reports_database_health(plugin):
    plugin, _ = plugin

    reply = (await _collect(plugin.diag(_Event())))[-1]

    assert "quick_check" in reply or "db_version" in reply
    assert "ok" in reply.lower()


@pytest.mark.asyncio
async def test_webui_command_points_to_the_dashboard(plugin):
    plugin, _ = plugin

    reply = (await _collect(plugin.webui(_Event())))[-1]

    assert "WebUI" in reply
    assert "LivingMemory" in reply


@pytest.mark.asyncio
async def test_migrate_scope_dry_run_describes_plan(plugin):
    plugin, _ = plugin
    await plugin.initializer.memory_engine.add_memory(
        "User visited Hangzhou",
        session_id="host:FriendMessage:user-9",
        persona_id=None,
    )

    replies = await _collect(plugin.migrate_scope(_Event(), "global"))

    assert any("迁移预览" in reply for reply in replies)
    assert any("1" in reply for reply in replies)


@pytest.mark.asyncio
async def test_summarize_refuses_single_message(plugin):
    plugin, _ = plugin

    reply = (await _collect(plugin.summarize(_Event(), 1)))[-1]

    assert "大于等于 2" in reply


@pytest.mark.asyncio
async def test_private_chat_message_is_stored_for_later_reflection(plugin):
    """私聊钩子把用户消息写入会话库，后续总结才有内容可用。"""
    plugin, _ = plugin
    event = _Event()
    request = SimpleNamespace(prompt="remember that I like jasmine tea", extra_user_content_parts=[])

    await plugin.handle_memory_recall(event, request)

    manager = plugin.initializer.conversation_manager
    messages = await manager.get_context(
        event.unified_msg_origin, max_messages=10, format_for_llm=False
    )
    texts = [
        message.get("content") if isinstance(message, dict) else message.content
        for message in messages
    ]
    assert "remember that I like jasmine tea" in texts


@pytest.mark.asyncio
async def test_blacklisted_group_cannot_write_memories(monkeypatch, tmp_path):
    config = dict(CONFIG)
    config["filtering_settings"] = {
        "memory_scope_mode": "global",
        "group_blacklist": ["blocked-group"],
    }
    plugin, _ = await _boot_plugin(monkeypatch, tmp_path, config)
    try:
        memorize = next(
            tool
            for tool in plugin.context.llm_tools
            if tool.name == "memorize_long_term_memory"
        )
        event = _Event(umo="host:GroupMessage:blocked-group", group_id="blocked-group")

        result = await memorize.call(
            SimpleNamespace(context=SimpleNamespace(event=event)),
            memory="should not be stored",
        )

        assert "not allowed" in result
        stats = await plugin.initializer.memory_engine.get_statistics()
        assert stats["total_memories"] == 0
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_forget_unknown_memory_reports_failure(plugin):
    plugin, _ = plugin

    reply = (await _collect(plugin.forget(_Event(), 9999)))[-1]

    assert "失败" in reply or "未找到" in reply


@pytest.mark.asyncio
async def test_boot_upgrade_skips_zero_byte_conversations_db(monkeypatch, tmp_path):
    (tmp_path / ".plugin_version").write_text("2.12.0", encoding="utf-8")
    (tmp_path / "conversations.db").write_bytes(b"")
    (tmp_path / "decay_state.json").write_text('{"ok": true}', encoding="utf-8")

    plugin, _ = await _boot_plugin(monkeypatch, tmp_path)
    try:
        backup_dir = tmp_path / "backups" / "v2.12.0"
        assert backup_dir.is_dir()
        assert (backup_dir / "decay_state.json").is_file()
        assert not (backup_dir / "conversations.db").exists()
        assert (tmp_path / "conversations.db").stat().st_size > 0
        version = (tmp_path / ".plugin_version").read_text(encoding="utf-8").strip()
        assert version != "2.12.0"
    finally:
        await plugin.terminate()


@pytest.mark.asyncio
async def test_boot_continues_when_version_backup_fails_and_preserves_state(
    monkeypatch, tmp_path
):
    (tmp_path / ".plugin_version").write_text("2.12.0", encoding="utf-8")
    # Corrupted unused graph DB causes BackupManager transactional backup to abort.
    (tmp_path / "livingmemory_graph_documents.db").write_bytes(
        b"corrupted sqlite bytes"
    )
    existing_backup = tmp_path / "backups" / "v2.12.0"
    existing_backup.mkdir(parents=True)
    (existing_backup / "keep.txt").write_text("prior-backup", encoding="utf-8")

    plugin, _ = await _boot_plugin(monkeypatch, tmp_path)
    try:
        version = (tmp_path / ".plugin_version").read_text(encoding="utf-8").strip()
        assert version == "2.12.0"
        marker = (existing_backup / "keep.txt").read_text(encoding="utf-8")
        assert marker == "prior-backup"
    finally:
        await plugin.terminate()

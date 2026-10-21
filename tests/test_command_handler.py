"""
Tests for CommandHandler.
"""

import inspect
import json
from unittest.mock import AsyncMock, Mock

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.command_handler import CommandHandler


@pytest.fixture
def config_manager():
    return ConfigManager()


@pytest.fixture
def memory_engine():
    engine = Mock()
    engine.db_path = "/tmp/livingmemory-test.db"
    engine.get_statistics = AsyncMock(
        return_value={
            "total_memories": 2,
            "sessions": {"s1": 1, "s2": 1},
            "newest_memory": 1_700_000_000.0,
        }
    )
    engine.search_memories = AsyncMock(return_value=[])
    engine.delete_memory = AsyncMock(return_value=True)
    engine.rebuild_graph_index = AsyncMock(return_value={"rebuilt": 0, "skipped": 0})
    return engine


@pytest.fixture
def conversation_manager():
    manager = Mock()
    manager.clear_session = AsyncMock()
    return manager


@pytest.fixture
def index_validator():
    validator = Mock()
    validator.check_consistency = AsyncMock(
        return_value=Mock(
            is_consistent=True,
            needs_rebuild=False,
            reason="ok",
            documents_count=2,
            bm25_count=2,
            vector_count=2,
        )
    )
    validator.rebuild_indexes = AsyncMock(
        return_value={"success": True, "processed": 2, "errors": 0, "total": 2}
    )
    return validator


@pytest.fixture
def handler(config_manager, memory_engine, conversation_manager, index_validator):
    context = Mock()
    return CommandHandler(
        context=context,
        config_manager=config_manager,
        memory_engine=memory_engine,
        conversation_manager=conversation_manager,
        index_validator=index_validator,
        initialization_status_callback=lambda: "ready",
    )


@pytest.mark.asyncio
async def test_handle_status_returns_report(handler, mock_event):
    messages = [msg async for msg in handler.handle_status(mock_event)]
    assert len(messages) == 1
    assert "LivingMemory" in messages[0]
    assert "总记忆数" in messages[0]


@pytest.mark.asyncio
async def test_handle_status_without_engine_returns_actionable_message(
    config_manager, mock_event
):
    handler = CommandHandler(
        context=Mock(),
        config_manager=config_manager,
        memory_engine=None,
        conversation_manager=None,
        index_validator=None,
    )

    messages = [msg async for msg in handler.handle_status(mock_event)]
    assert len(messages) == 1
    assert "/lmem status 执行失败" in messages[0]
    assert "检查插件状态" in messages[0]


@pytest.mark.asyncio
async def test_handle_status_error_contains_suggestions(
    handler, mock_event, memory_engine
):
    memory_engine.get_statistics = AsyncMock(side_effect=RuntimeError("db unavailable"))

    messages = [msg async for msg in handler.handle_status(mock_event)]
    assert len(messages) == 1
    assert "获取状态失败" in messages[0]
    assert "建议排查" in messages[0]
    assert "数据库文件可读写" in messages[0]


@pytest.mark.asyncio
async def test_handle_search_validates_inputs_and_calls_engine(handler, mock_event):
    empty = [msg async for msg in handler.handle_search(mock_event, "", 3)]
    assert "不能为空" in empty[0]

    _ = [msg async for msg in handler.handle_search(mock_event, "hello", 200)]
    # k should be clamped to 100.
    handler.memory_engine.search_memories.assert_awaited_with(
        query="hello", k=100, session_id=mock_event.unified_msg_origin
    )


@pytest.mark.asyncio
async def test_handle_search_renders_results(handler, mock_event, memory_engine):
    result = Mock(doc_id=7, final_score=0.88, content="hello memory")
    memory_engine.search_memories = AsyncMock(return_value=[result])

    messages = [msg async for msg in handler.handle_search(mock_event, "hello", 5)]
    assert len(messages) == 1
    assert "找到 1 条相关记忆" in messages[0]
    assert "ID: 7" in messages[0]


@pytest.mark.asyncio
async def test_handle_forget_success_and_not_found(handler, mock_event, memory_engine):
    success = [msg async for msg in handler.handle_forget(mock_event, 10)]
    assert "已删除记忆 #10" in success[0]

    memory_engine.delete_memory = AsyncMock(return_value=False)
    failed = [msg async for msg in handler.handle_forget(mock_event, 11)]
    assert "删除失败" in failed[0]


@pytest.mark.asyncio
async def test_handle_rebuild_index_branches(handler, mock_event, index_validator):
    # no rebuild needed
    msgs = [msg async for msg in handler.handle_rebuild_index(mock_event)]
    assert any("索引状态正常" in msg for msg in msgs)

    # rebuild needed
    index_validator.check_consistency = AsyncMock(
        return_value=Mock(
            is_consistent=False,
            needs_rebuild=True,
            reason="inconsistent",
            documents_count=3,
            bm25_count=2,
            vector_count=1,
        )
    )
    msgs2 = [msg async for msg in handler.handle_rebuild_index(mock_event)]
    assert any("开始重建索引" in msg for msg in msgs2)
    assert index_validator.rebuild_indexes.await_count >= 1


@pytest.mark.asyncio
async def test_handle_rebuild_index_failed_result_contains_retry_hint(
    handler, mock_event, index_validator
):
    index_validator.check_consistency = AsyncMock(
        return_value=Mock(
            is_consistent=False,
            needs_rebuild=True,
            reason="inconsistent",
            documents_count=3,
            bm25_count=2,
            vector_count=1,
        )
    )
    index_validator.rebuild_indexes = AsyncMock(
        return_value={"success": False, "message": "vector unavailable"}
    )

    messages = [msg async for msg in handler.handle_rebuild_index(mock_event)]
    assert any("索引重建失败" in msg for msg in messages)
    assert any("/lmem rebuild-index" in msg for msg in messages)


@pytest.mark.asyncio
async def test_handle_reset_and_help(handler, mock_event, conversation_manager):
    reset = [msg async for msg in handler.handle_reset(mock_event)]
    assert "已重置" in reset[0]
    conversation_manager.clear_session.assert_awaited_once()

    help_msg = [msg async for msg in handler.handle_help(mock_event)]
    assert "/lmem status" in help_msg[0]
    assert (
        "https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory"
        in help_msg[0]
    )


@pytest.mark.asyncio
async def test_handle_webui_shows_guide(handler, mock_event):
    messages = [msg async for msg in handler.handle_webui(mock_event)]
    assert len(messages) == 1
    assert "AstrBot" in messages[0]
    assert "Plugins" in messages[0] or "插件" in messages[0]
    assert "Pages -> dashboard" in messages[0]


@pytest.mark.asyncio
async def test_handle_cleanup_invalid_history_json_returns_clear_error(
    config_manager, memory_engine, conversation_manager, index_validator, mock_event
):
    context = Mock()
    context.conversation_manager = Mock()
    context.conversation_manager.get_curr_conversation_id = AsyncMock(
        return_value="cid-1"
    )
    context.conversation_manager.get_conversation = AsyncMock(
        return_value=Mock(history="{bad json")
    )
    context.conversation_manager.update_conversation = AsyncMock()

    handler = CommandHandler(
        context=context,
        config_manager=config_manager,
        memory_engine=memory_engine,
        conversation_manager=conversation_manager,
        index_validator=index_validator,
    )

    messages = [msg async for msg in handler.handle_cleanup(mock_event, dry_run=True)]
    assert any("解析对话历史失败" in msg for msg in messages)
    assert any("有效 JSON" in msg for msg in messages)


@pytest.mark.asyncio
async def test_handle_search_renders_dual_route_breakdown(
    handler, mock_event, memory_engine
):
    result = Mock(
        doc_id=8,
        final_score=0.91,
        content="graph memory",
        score_breakdown={
            "document_keyword_score": 0.11,
            "document_vector_score": 0.22,
            "graph_keyword_score": 0.33,
            "graph_vector_score": 0.44,
        },
    )
    memory_engine.search_memories = AsyncMock(return_value=[result])

    messages = [msg async for msg in handler.handle_search(mock_event, "graph", 5)]
    assert len(messages) == 1
    assert "0.11" in messages[0]
    assert "0.22" in messages[0]
    assert "0.33" in messages[0]
    assert "0.44" in messages[0]


@pytest.mark.asyncio
async def test_handle_rebuild_graph_reports_progress_and_summary(
    handler, mock_event, memory_engine
):
    memory_engine.rebuild_graph_index = AsyncMock(
        return_value={"rebuilt": 3, "skipped": 1}
    )

    messages = [msg async for msg in handler.handle_rebuild_graph(mock_event)]
    assert len(messages) == 2
    memory_engine.rebuild_graph_index.assert_awaited_once()
    assert messages[0].endswith("...")
    assert [
        part for part in messages[1].split() if any(ch.isdigit() for ch in part)
    ] == ["3", "1"]


OLD_SCOPE = "test:private:session-old"
OTHER_SCOPE = "test:private:session-other"
TARGET_SCOPE = "livingmemory:global"


def _parse_like_astrbot(handler_fn, args_text: str) -> dict[str, str]:
    """按 AstrBot CommandFilter 的规则解析位置参数：丢弃空 token、缺失参数取默认值。

    AstrBot 在 ``astrbot/core/star/filter/command.py`` 中先 ``split(" ")`` 再
    过滤空字符串，因此这里复刻同样的行为来验证命令参数顺序真的可用。
    """
    parameters = list(inspect.signature(handler_fn).parameters.values())[1:]
    tokens = [token for token in args_text.split(" ") if token]
    return {
        parameter.name: tokens[index] if index < len(tokens) else parameter.default
        for index, parameter in enumerate(parameters)
    }


async def _run_migrate_scope(handler, event, args_text: str) -> list[str]:
    params = _parse_like_astrbot(handler.handle_migrate_scope, args_text)
    return [
        message async for message in handler.handle_migrate_scope(event, **params)
    ]


async def _make_migration_handler(
    tmp_path, doc_scopes, atom_scopes=None, user_config=None
):
    """建最小库（documents + 可选 memory_atoms）并组装带真实连接的 CommandHandler。"""
    db_path = tmp_path / "lm.db"
    conn = await aiosqlite.connect(db_path)
    await conn.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, metadata TEXT)"
    )
    for index, scope in enumerate(doc_scopes, start=1):
        await conn.execute(
            "INSERT INTO documents (id, metadata) VALUES (?, ?)",
            (index, json.dumps({"session_id": scope}, ensure_ascii=False)),
        )
    if atom_scopes is not None:
        await conn.execute(
            "CREATE TABLE memory_atoms (id INTEGER PRIMARY KEY, session_id TEXT)"
        )
        for index, scope in enumerate(atom_scopes, start=1):
            await conn.execute(
                "INSERT INTO memory_atoms (id, session_id) VALUES (?, ?)",
                (index, scope),
            )
    await conn.commit()

    engine = Mock()
    engine.db_path = str(db_path)
    engine.db_connection = conn
    handler = CommandHandler(
        context=Mock(),
        config_manager=ConfigManager(user_config or {}),
        memory_engine=engine,
        conversation_manager=None,
        index_validator=None,
    )
    return handler, conn, db_path


async def _doc_scopes(conn) -> dict[str, int]:
    cursor = await conn.execute(
        "SELECT json_extract(metadata, '$.session_id') AS scope, COUNT(*) "
        "FROM documents GROUP BY scope"
    )
    return {str(scope): int(count) for scope, count in await cursor.fetchall()}


async def _atom_scopes(conn) -> dict[str, int]:
    cursor = await conn.execute(
        "SELECT session_id AS scope, COUNT(*) FROM memory_atoms GROUP BY scope"
    )
    return {str(scope): int(count) for scope, count in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_handle_migrate_scope_accepts_apply_after_target(tmp_path, mock_event):
    """B1：`/lmem migrate-scope global apply` 必须是批量写入，而不是把 apply 当 source。"""
    handler, conn, db_path = await _make_migration_handler(
        tmp_path, [OLD_SCOPE, OLD_SCOPE], atom_scopes=[OLD_SCOPE]
    )
    try:
        messages = await _run_migrate_scope(handler, mock_event, "global apply")
        joined = "\n".join(messages)
        assert "迁移完成" in joined
        assert "已经位于目标作用域" not in joined

        assert await _doc_scopes(conn) == {TARGET_SCOPE: 2}
        assert await _atom_scopes(conn) == {TARGET_SCOPE: 1}
        assert list((db_path.parent / "backups").glob("scope_migration_*.db"))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_handle_migrate_scope_defaults_to_dry_run(tmp_path, mock_event):
    """B1：不传 apply（或显式 dry-run）时不得写库。"""
    handler, conn, db_path = await _make_migration_handler(
        tmp_path, [OLD_SCOPE], atom_scopes=[OLD_SCOPE]
    )
    try:
        for args_text in ("global", "global dry-run"):
            messages = await _run_migrate_scope(handler, mock_event, args_text)
            assert "未写入任何数据" in "\n".join(messages)
            assert await _doc_scopes(conn) == {OLD_SCOPE: 1}
            assert await _atom_scopes(conn) == {OLD_SCOPE: 1}
        assert not (db_path.parent / "backups").exists()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_handle_migrate_scope_applies_only_requested_source(tmp_path, mock_event):
    """B1：`<target> apply <source>` 仅迁移指定来源作用域。"""
    handler, conn, _ = await _make_migration_handler(
        tmp_path, [OLD_SCOPE, OTHER_SCOPE], atom_scopes=[OLD_SCOPE, OTHER_SCOPE]
    )
    try:
        messages = await _run_migrate_scope(
            handler, mock_event, f"global apply {OLD_SCOPE}"
        )
        assert "迁移完成" in "\n".join(messages)
        assert await _doc_scopes(conn) == {TARGET_SCOPE: 1, OTHER_SCOPE: 1}
        assert await _atom_scopes(conn) == {TARGET_SCOPE: 1, OTHER_SCOPE: 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_token", ["all", "*", "-"])
async def test_handle_migrate_scope_all_source_tokens_mean_bulk(
    tmp_path, mock_event, source_token
):
    handler, conn, _ = await _make_migration_handler(
        tmp_path, [OLD_SCOPE, OTHER_SCOPE]
    )
    try:
        messages = await _run_migrate_scope(
            handler, mock_event, f"global apply {source_token}"
        )
        assert "迁移完成" in "\n".join(messages)
        assert await _doc_scopes(conn) == {TARGET_SCOPE: 2}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_handle_migrate_scope_rejects_unknown_action_with_usage(
    tmp_path, mock_event
):
    """B1：未知的第二步 token 不能被当成 source，直接回显正确用法且不写库。"""
    handler, conn, _ = await _make_migration_handler(tmp_path, [OLD_SCOPE])
    try:
        messages = await _run_migrate_scope(handler, mock_event, "global explode")
        assert "用法" in "\n".join(messages)
        assert await _doc_scopes(conn) == {OLD_SCOPE: 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_handle_migrate_scope_noop_messages_are_distinct(tmp_path, mock_event):
    """B1：'全部已在目标作用域' 与 'source 过滤没命中' 必须是两条不同的提示。"""
    handler, conn, _ = await _make_migration_handler(
        tmp_path, [TARGET_SCOPE], atom_scopes=[TARGET_SCOPE]
    )
    try:
        all_done = await _run_migrate_scope(handler, mock_event, "global")
        all_done_text = "\n".join(all_done)
        assert "已经位于目标作用域" in all_done_text
        assert "未匹配到" not in all_done_text

        unmatched = await _run_migrate_scope(
            handler, mock_event, "global apply missing-scope"
        )
        unmatched_text = "\n".join(unmatched)
        assert "未匹配到" in unmatched_text
        assert "missing-scope" in unmatched_text
        assert "已经位于目标作用域" not in unmatched_text

        # 两次都没有写库
        assert await _doc_scopes(conn) == {TARGET_SCOPE: 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_handle_migrate_scope_user_target_honours_identity_aliases(
    tmp_path, mock_event
):
    """M4：user 目标必须应用 access_control.identity_aliases，否则数据仍然失联。"""
    handler, conn, _ = await _make_migration_handler(
        tmp_path,
        [OLD_SCOPE],
        user_config={
            "access_control": {"identity_aliases": "test:user-1=canonical-user"}
        },
    )
    try:
        messages = await _run_migrate_scope(handler, mock_event, "user apply")
        assert "迁移完成" in "\n".join(messages)
        assert await _doc_scopes(conn) == {
            "livingmemory:user:test:canonical-user": 1
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_handle_migrate_scope_notes_missing_optional_tables(
    tmp_path, mock_event
):
    """M1：图记忆关闭（缺 memory_atoms / graph_entries）时命令仍可用并提示跳过。"""
    handler, conn, _ = await _make_migration_handler(tmp_path, [OLD_SCOPE])
    try:
        messages = await _run_migrate_scope(handler, mock_event, "global apply")
        joined = "\n".join(messages)
        assert "memory_atoms" in joined
        assert "graph_entries" in joined
        assert "迁移完成" in joined
        assert await _doc_scopes(conn) == {TARGET_SCOPE: 1}
    finally:
        await conn.close()

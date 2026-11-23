"""记忆作用域迁移（core/managers/scope_migrator.py）测试。

覆盖：
- survey：按作用域盘点存量
- plan：只读、正确计数、支持来源过滤
- execute：改写两处作用域、幂等、保留 old_session_id 便于追溯
- backup：生成一致性快照
"""

from __future__ import annotations

import json
import sqlite3

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.managers.scope_migrator import (
    ScopeMigrator,
)

OLD_PRIVATE = "bot:FriendMessage:user-1"
OLD_GROUP = "bot:GroupMessage:group-1"
TARGET = "livingmemory:global"


async def _make_db(tmp_path, doc_scopes, atom_scopes):
    """建最小库：documents + memory_atoms。"""
    conn = await aiosqlite.connect(tmp_path / "lm.db")
    await conn.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
    )
    await conn.execute(
        "CREATE TABLE memory_atoms "
        "(id INTEGER PRIMARY KEY, content TEXT, session_id TEXT)"
    )
    for idx, scope in enumerate(doc_scopes, start=1):
        await conn.execute(
            "INSERT INTO documents (id, text, metadata) VALUES (?, ?, ?)",
            (idx, f"doc{idx}", json.dumps({"session_id": scope}, ensure_ascii=False)),
        )
    for idx, scope in enumerate(atom_scopes, start=1):
        await conn.execute(
            "INSERT INTO memory_atoms (id, content, session_id) VALUES (?, ?, ?)",
            (idx, f"atom{idx}", scope),
        )
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_survey_counts_by_scope(tmp_path):
    conn = await _make_db(
        tmp_path,
        [OLD_PRIVATE, OLD_PRIVATE, OLD_GROUP, TARGET],
        [OLD_PRIVATE, OLD_GROUP, OLD_GROUP],
    )
    try:
        breakdown = await ScopeMigrator(conn).survey()
        assert breakdown.documents == {OLD_PRIVATE: 2, OLD_GROUP: 1, TARGET: 1}
        assert breakdown.atoms == {OLD_PRIVATE: 1, OLD_GROUP: 2}
        assert breakdown.total == 7
        assert TARGET in breakdown.scopes
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_plan_is_read_only(tmp_path):
    conn = await _make_db(tmp_path, [OLD_PRIVATE, OLD_GROUP], [OLD_PRIVATE])
    try:
        migrator = ScopeMigrator(conn)
        plan = await migrator.plan(TARGET)
        assert plan.documents == 2
        assert plan.atoms == 1
        assert plan.total == 3
        # 确认没有任何写入
        breakdown = await migrator.survey()
        assert breakdown.documents == {OLD_PRIVATE: 1, OLD_GROUP: 1}
        assert breakdown.atoms == {OLD_PRIVATE: 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_plan_skips_records_already_on_target(tmp_path):
    conn = await _make_db(tmp_path, [TARGET, TARGET, OLD_GROUP], [TARGET])
    try:
        plan = await ScopeMigrator(conn).plan(TARGET)
        assert plan.documents == 1  # 已处于目标的 2 条不计入
        assert plan.atoms == 0
        assert plan.by_source == {OLD_GROUP: 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_plan_respects_source_filter(tmp_path):
    conn = await _make_db(
        tmp_path, [OLD_PRIVATE, OLD_GROUP, OLD_GROUP], [OLD_PRIVATE, OLD_GROUP]
    )
    try:
        plan = await ScopeMigrator(conn).plan(TARGET, source=OLD_GROUP)
        assert plan.documents == 2
        assert plan.atoms == 1
        assert plan.by_source == {OLD_GROUP: 3}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_plan_rejects_empty_target(tmp_path):
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    try:
        with pytest.raises(ValueError):
            await ScopeMigrator(conn).plan("   ")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_migrates_both_tables(tmp_path):
    conn = await _make_db(
        tmp_path, [OLD_PRIVATE, OLD_PRIVATE, OLD_GROUP], [OLD_PRIVATE, OLD_GROUP]
    )
    try:
        migrator = ScopeMigrator(conn)
        plan = await migrator.plan(TARGET)
        result = await migrator.execute(plan)

        assert result.documents == 3
        assert result.atoms == 2
        assert result.total == 5

        breakdown = await migrator.survey()
        assert breakdown.documents == {TARGET: 3}
        assert breakdown.atoms == {TARGET: 2}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_is_idempotent(tmp_path):
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [OLD_PRIVATE])
    try:
        migrator = ScopeMigrator(conn)
        first = await migrator.execute(await migrator.plan(TARGET))
        assert first.total == 2

        second_plan = await migrator.plan(TARGET)
        assert second_plan.is_noop
        second = await migrator.execute(second_plan)
        assert second.total == 0

        breakdown = await migrator.survey()
        assert breakdown.documents == {TARGET: 1}
        assert breakdown.atoms == {TARGET: 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_keeps_old_session_id_for_traceability(tmp_path):
    conn = await _make_db(tmp_path, [OLD_GROUP], [])
    try:
        migrator = ScopeMigrator(conn)
        await migrator.execute(await migrator.plan(TARGET))

        cursor = await conn.execute("SELECT metadata FROM documents")
        (metadata_str,) = await cursor.fetchone()
        metadata = json.loads(metadata_str)
        assert metadata["session_id"] == TARGET
        assert metadata["old_session_id"] == OLD_GROUP
        assert "migrated_at" in metadata
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_source_filter_only_touches_that_scope(tmp_path):
    conn = await _make_db(tmp_path, [OLD_PRIVATE, OLD_GROUP], [OLD_PRIVATE, OLD_GROUP])
    try:
        migrator = ScopeMigrator(conn)
        await migrator.execute(await migrator.plan(TARGET, source=OLD_PRIVATE))

        breakdown = await migrator.survey()
        assert breakdown.documents.get(OLD_GROUP) == 1
        assert breakdown.documents.get(TARGET) == 1
        assert breakdown.atoms.get(OLD_GROUP) == 1
        assert breakdown.atoms.get(TARGET) == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backup_creates_snapshot(tmp_path):
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [OLD_PRIVATE])
    try:
        backup = tmp_path / "backup" / "snapshot.db"
        await ScopeMigrator.backup_to(conn, backup)
        assert backup.exists()
        assert backup.stat().st_size > 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backup_refuses_to_overwrite(tmp_path):
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    try:
        backup = tmp_path / "snapshot.db"
        backup.write_bytes(b"existing")
        with pytest.raises(FileExistsError):
            await ScopeMigrator.backup_to(conn, backup)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_records_without_scope_are_counted_and_migrated(tmp_path):
    """session_id 缺失/为 NULL 的记录同样应被纳入迁移。"""
    conn = await aiosqlite.connect(tmp_path / "lm.db")
    await conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, metadata TEXT)")
    await conn.execute(
        "CREATE TABLE memory_atoms (id INTEGER PRIMARY KEY, session_id TEXT)"
    )
    await conn.execute("INSERT INTO documents (id, metadata) VALUES (1, ?)", ("{}",))
    await conn.execute("INSERT INTO documents (id, metadata) VALUES (2, NULL)")
    await conn.execute("INSERT INTO memory_atoms (id, session_id) VALUES (1, NULL)")
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        plan = await migrator.plan(TARGET)
        assert plan.documents == 2
        assert plan.atoms == 1

        await migrator.execute(plan)
        breakdown = await migrator.survey()
        assert breakdown.documents == {TARGET: 2}
        assert breakdown.atoms == {TARGET: 1}
    finally:
        await conn.close()


async def _make_db_with_graph(tmp_path, doc_scopes, atom_scopes, graph_scopes):
    """三表齐全的最小库（图记忆开启时的真实结构）。"""
    conn = await _make_db(tmp_path, doc_scopes, atom_scopes)
    await conn.execute(
        "CREATE TABLE graph_entries (id INTEGER PRIMARY KEY, session_id TEXT)"
    )
    for idx, scope in enumerate(graph_scopes, start=1):
        await conn.execute(
            "INSERT INTO graph_entries (id, session_id) VALUES (?, ?)", (idx, scope)
        )
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_missing_optional_tables_are_skipped(tmp_path):
    """图记忆关闭时 memory_atoms / graph_entries 不存在，文档迁移仍应可用（M1）。"""
    conn = await aiosqlite.connect(tmp_path / "lm.db")
    await conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, metadata TEXT)")
    await conn.execute(
        "INSERT INTO documents (id, metadata) VALUES (1, ?)",
        (json.dumps({"session_id": OLD_PRIVATE}),),
    )
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        breakdown = await migrator.survey()
        assert breakdown.documents == {OLD_PRIVATE: 1}
        assert breakdown.atoms == {}
        assert breakdown.graph_entries == {}
        assert breakdown.missing_tables == [
            "memory_atoms",
            "graph_entries",
            "memory_profiles",
        ]
        assert breakdown.total == 1

        plan = await migrator.plan(TARGET)
        assert plan.documents == 1
        assert plan.total == 1

        result = await migrator.execute(plan)
        assert result.documents == 1
        assert result.atoms == 0
        assert result.graph_entries == 0
        assert (await migrator.survey()).documents == {TARGET: 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_migrates_graph_entries(tmp_path):
    """graph_entries.session_id 是第三份 scope 副本，必须一并迁移（M3）。"""
    conn = await _make_db_with_graph(
        tmp_path, [OLD_PRIVATE], [OLD_PRIVATE], [OLD_PRIVATE, TARGET]
    )
    try:
        migrator = ScopeMigrator(conn)
        breakdown = await migrator.survey()
        assert breakdown.graph_entries == {OLD_PRIVATE: 1, TARGET: 1}
        assert breakdown.total == 4  # 1 文档 + 1 原子 + 2 图记忆

        plan = await migrator.plan(TARGET)
        assert plan.documents == 1
        assert plan.atoms == 1
        assert plan.graph_entries == 1
        assert plan.total == 3

        result = await migrator.execute(plan)
        assert result.documents == 1
        assert result.atoms == 1
        assert result.graph_entries == 1
        assert result.remaining == 0

        breakdown = await migrator.survey()
        assert breakdown.documents == {TARGET: 1}
        assert breakdown.atoms == {TARGET: 1}
        assert breakdown.graph_entries == {TARGET: 2}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_rolls_back_all_tables_when_a_later_update_fails(tmp_path):
    """第三条 UPDATE 失败时，前两条也不能残留（M2）。"""
    conn = await _make_db_with_graph(
        tmp_path, [OLD_PRIVATE], [OLD_PRIVATE], [OLD_PRIVATE]
    )
    await conn.execute(
        "CREATE TRIGGER block_graph_update BEFORE UPDATE ON graph_entries "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END;"
    )
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        plan = await migrator.plan(TARGET)
        with pytest.raises(sqlite3.IntegrityError):
            await migrator.execute(plan)

        breakdown = await migrator.survey()
        assert breakdown.documents == {OLD_PRIVATE: 1}
        assert breakdown.atoms == {OLD_PRIVATE: 1}
        assert breakdown.graph_entries == {OLD_PRIVATE: 1}
        assert not conn.in_transaction
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_backup_runs_outside_pending_transaction(tmp_path):
    """共享连接残留未提交写入时，VACUUM INTO 备份也不能失败（M2）。"""
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    # 模拟其他协程留在共享连接上的未提交写入：VACUUM INTO 在事务中会直接报错
    await conn.execute(
        "INSERT INTO documents (id, metadata) VALUES (2, ?)",
        (json.dumps({"session_id": TARGET}),),
    )
    backup = tmp_path / "backups" / "snapshot.db"
    try:
        migrator = ScopeMigrator(conn)
        plan = await migrator.plan(TARGET)
        result = await migrator.execute(plan, backup_path=backup)

        assert backup.exists()
        assert result.documents == 1
        assert result.remaining == 0

        snapshot = await aiosqlite.connect(backup)
        try:
            cursor = await snapshot.execute("SELECT COUNT(*) FROM documents")
            assert (await cursor.fetchone())[0] == 2
        finally:
            await snapshot.close()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_malformed_and_non_object_metadata_are_normalized_and_migrated(tmp_path):
    """坏 JSON / 非对象根之前会被计数但静默不改，现在应归一化后真正迁移（m1/m2）。"""
    conn = await aiosqlite.connect(tmp_path / "lm.db")
    await conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, metadata TEXT)")
    rows = ["{bad json", "[]", "5", "null", '"text"']
    for idx, metadata in enumerate(rows, start=1):
        await conn.execute(
            "INSERT INTO documents (id, metadata) VALUES (?, ?)", (idx, metadata)
        )
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        breakdown = await migrator.survey()  # 不应因坏 metadata 抛错
        assert breakdown.documents == {"": 5}

        plan = await migrator.plan(TARGET)
        assert plan.documents == 5
        result = await migrator.execute(plan)
        assert result.documents == 5
        assert result.remaining == 0

        cursor = await conn.execute("SELECT id, metadata FROM documents ORDER BY id")
        for _, metadata_str in await cursor.fetchall():
            metadata = json.loads(metadata_str)
            assert isinstance(metadata, dict)
            assert metadata["session_id"] == TARGET

        assert (await migrator.survey()).documents == {TARGET: 5}
    finally:
        await conn.close()


async def _make_profiles(conn):
    await conn.execute(
        "CREATE TABLE memory_profiles ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, persona_id TEXT, "
        "profile_json TEXT NOT NULL DEFAULT '{}', UNIQUE(scope, persona_id))"
    )


@pytest.mark.asyncio
async def test_profiles_are_migrated_with_their_scope(tmp_path):
    """用户档案按 scope 存储，漏迁移会让档案在换作用域后失联（#373）。"""
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    await _make_profiles(conn)
    await conn.execute(
        "INSERT INTO memory_profiles (scope, persona_id) VALUES (?, ?)",
        (OLD_PRIVATE, "p1"),
    )
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        plan = await migrator.plan(TARGET)
        assert plan.profiles == 1

        result = await migrator.execute(plan)
        assert result.profiles == 1

        cursor = await conn.execute("SELECT scope, persona_id FROM memory_profiles")
        assert await cursor.fetchall() == [(TARGET, "p1")]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_profile_conflicts_are_skipped_instead_of_failing(tmp_path):
    """目标作用域已有同一人格的档案时跳过该行，避免唯一约束让整次迁移失败。"""
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    await _make_profiles(conn)
    await conn.execute(
        "INSERT INTO memory_profiles (scope, persona_id) VALUES (?, ?)",
        (OLD_PRIVATE, "p1"),
    )
    await conn.execute(
        "INSERT INTO memory_profiles (scope, persona_id) VALUES (?, ?)",
        (TARGET, "p1"),
    )
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        result = await migrator.execute(await migrator.plan(TARGET))

        assert result.profiles == 0
        # 冲突行留在原作用域，由 remaining 如实上报
        assert result.remaining == 1
        cursor = await conn.execute(
            "SELECT scope FROM memory_profiles WHERE persona_id = 'p1' ORDER BY scope"
        )
        assert [row[0] for row in await cursor.fetchall()] == sorted(
            [OLD_PRIVATE, TARGET]
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_multiple_source_profiles_same_persona_migrate_latest_without_unique_error(
    tmp_path,
):
    """多条不同旧作用域的同人格档案迁往同一 target 时，仅迁最新 1 条不触发唯一约束（#487）。"""
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    await conn.execute(
        "CREATE TABLE memory_profiles ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, persona_id TEXT, "
        "profile_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL DEFAULT 0, "
        "UNIQUE(scope, persona_id))"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX idx_profiles_scope_persona "
        "ON memory_profiles(scope, COALESCE(persona_id, ''))"
    )
    await conn.executemany(
        "INSERT INTO memory_profiles (scope, persona_id, profile_json, updated_at) "
        "VALUES (?, ?, ?, ?)",
        [
            ("legacy:s1", "p1", '{"ver": 1}', 100.0),
            ("legacy:s2", "p1", '{"ver": 3}', 300.0),
            ("legacy:s3", "p1", '{"ver": 2}', 200.0),
            # NULL 与空串在 idx_profiles_scope_persona 下等价，也应只迁最新 1 条
            ("legacy:s1", None, '{"kind": "null"}', 50.0),
            ("legacy:s2", "", '{"kind": "empty"}', 150.0),
        ],
    )
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        result = await migrator.execute(await migrator.plan(TARGET))

        assert result.documents == 1
        assert result.profiles == 2
        assert result.remaining == 3

        cursor = await conn.execute(
            "SELECT persona_id, profile_json FROM memory_profiles "
            "WHERE scope = ? ORDER BY COALESCE(persona_id, '')",
            (TARGET,),
        )
        assert await cursor.fetchall() == [
            ("", '{"kind": "empty"}'),
            ("p1", '{"ver": 3}'),
        ]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_profile_null_and_empty_persona_conflict_with_target_is_skipped(tmp_path):
    """目标已有 NULL persona 档案时，旧作用域的空串 persona 档案也应识别冲突并跳过（#487）。"""
    conn = await _make_db(tmp_path, [], [])
    await _make_profiles(conn)
    await conn.execute(
        "CREATE UNIQUE INDEX idx_profiles_scope_persona "
        "ON memory_profiles(scope, COALESCE(persona_id, ''))"
    )
    await conn.execute(
        "INSERT INTO memory_profiles (scope, persona_id) VALUES (?, ?)",
        (TARGET, None),
    )
    await conn.execute(
        "INSERT INTO memory_profiles (scope, persona_id) VALUES (?, ?)",
        (OLD_PRIVATE, ""),
    )
    await conn.commit()
    try:
        migrator = ScopeMigrator(conn)
        result = await migrator.execute(await migrator.plan(TARGET))

        assert result.profiles == 0
        assert result.remaining == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_graph_vector_documents_are_migrated(tmp_path):
    """图向量文档在独立库里，检索按 metadata.session_id 过滤，必须同步迁移。"""
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    graph_path = tmp_path / "graph_docs.db"
    graph_conn = await aiosqlite.connect(graph_path)
    await graph_conn.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
    )
    await graph_conn.execute(
        "INSERT INTO documents (id, text, metadata) VALUES (1, 'x', ?)",
        (json.dumps({"session_id": OLD_PRIVATE, "source_memory_id": 1}),),
    )
    await graph_conn.commit()
    await graph_conn.close()
    try:
        migrator = ScopeMigrator(conn, graph_doc_path=graph_path)
        breakdown = await migrator.survey()
        assert breakdown.graph_vectors == {OLD_PRIVATE: 1}

        plan = await migrator.plan(TARGET)
        assert plan.graph_vectors == 1
        assert plan.total == 2

        result = await migrator.execute(plan)
        assert result.graph_vectors == 1

        verify = await aiosqlite.connect(graph_path)
        try:
            cursor = await verify.execute("SELECT metadata FROM documents")
            (raw,) = await cursor.fetchone()
        finally:
            await verify.close()
        metadata = json.loads(raw)
        assert metadata["session_id"] == TARGET
        assert metadata["old_session_id"] == OLD_PRIVATE
        assert metadata["source_memory_id"] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_graph_document_db_is_reported_not_fatal(tmp_path):
    """图记忆关闭（或文件缺失）时照常迁移主库，只把缺失情况列出来。"""
    conn = await _make_db(tmp_path, [OLD_PRIVATE], [])
    try:
        migrator = ScopeMigrator(conn, graph_doc_path=tmp_path / "missing.db")
        breakdown = await migrator.survey()
        assert breakdown.graph_vectors == {}
        assert "graph vector documents" in breakdown.missing_tables

        result = await migrator.execute(await migrator.plan(TARGET))
        assert result.graph_vectors == 0
        assert result.documents == 1
    finally:
        await conn.close()

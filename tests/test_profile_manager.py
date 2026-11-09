"""用户档案管理器：更新规则、证据、置信度、待确认、历史与隔离。"""

from pathlib import Path

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.managers.profile_manager import (
    ProfileManager,
    normalize_profile_updates,
)

from .test_memory_engine import _FakeFaissDB

SCOPE = "test:private:profile-1"
PERSONA = "persona-1"


async def _make_engine(tmp_path: Path, **overrides) -> MemoryEngine:
    config = {
        "recent_memory_count": 0,
        "atom_enabled": False,
        "graph_memory_enabled": False,
    }
    config.update(overrides)
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_FakeFaissDB(),
        config=config,
    )
    await engine.initialize()
    return engine


async def _add_evidence(
    engine: MemoryEngine,
    content: str = "用户喜欢燕麦拿铁",
    scope: str = SCOPE,
    persona: str | None = PERSONA,
) -> int:
    return await engine.add_memory(
        content=content,
        session_id=scope,
        persona_id=persona,
        importance=0.8,
    )


def _set_update(
    field: str = "称呼",
    value: str = "小林",
    evidence: list[int] | None = None,
    confidence: float = 0.9,
    section: str = "basic",
    operation: str = "set",
) -> dict:
    return {
        "section": section,
        "field": field,
        "value": value,
        "operation": operation,
        "evidence": evidence or [],
        "confidence": confidence,
    }


@pytest.mark.asyncio
async def test_engine_initializes_profile_tables_and_manager(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        assert isinstance(engine.profile_manager, ProfileManager)
        cursor = await engine.db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('memory_profiles', 'memory_profile_history')"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert tables == {"memory_profiles", "memory_profile_history"}
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_set_applies_with_evidence_and_writes_history(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(engine)

        result = await manager.apply_updates(
            SCOPE,
            PERSONA,
            [_set_update(evidence=[memory_id])],
            evidence_memory_ids=[memory_id],
            source="reflection",
        )

        assert result == {"applied": 1, "pending": 0, "version": 1}
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["basic"]["称呼"] == "小林"
        assert profile["version"] == 1
        assert profile["pending"] == []
        history = await manager.get_history(profile["id"])
        assert len(history) == 1
        assert history[0]["version"] == 1
        assert history[0]["changed_fields"] == ["basic.称呼"]
        assert history[0]["evidence_memory_ids"] == [memory_id]
        assert history[0]["source"] == "reflection"
        assert history[0]["profile"]["basic"]["称呼"] == "小林"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_low_confidence_goes_pending_then_accept_applies(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(engine)

        result = await manager.apply_updates(
            SCOPE,
            PERSONA,
            [_set_update(evidence=[memory_id], confidence=0.3)],
            evidence_memory_ids=[memory_id],
        )
        assert result == {"applied": 0, "pending": 1, "version": 0}
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["basic"] == {}

        pending = await manager.list_pending(SCOPE, PERSONA)
        assert pending["pending"][0]["index"] == 0
        assert pending["pending"][0]["field"] == "称呼"

        updated = await manager.resolve_pending(
            SCOPE, PERSONA, 0, accept=True
        )
        assert updated["profile"]["basic"]["称呼"] == "小林"
        assert updated["version"] == 1
        assert updated["pending"] == []
        history = await manager.get_history(updated["id"])
        assert history[0]["source"] == "pending_accept"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reject_pending_keeps_profile_unchanged(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        await manager.apply_updates(
            SCOPE,
            PERSONA,
            [_set_update(confidence=0.2)],
            evidence_memory_ids=[],
        )
        updated = await manager.resolve_pending(SCOPE, PERSONA, 0, accept=False)
        assert updated["profile"]["basic"] == {}
        assert updated["pending"] == []
        assert updated["version"] == 0
        history = await manager.get_history(updated["id"])
        assert history == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_missing_or_unknown_evidence_stays_pending(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        result = await manager.apply_updates(
            SCOPE,
            PERSONA,
            [_set_update(evidence=[]), _set_update(field="职业", evidence=[999999])],
            evidence_memory_ids=[],
        )
        assert result["applied"] == 0
        assert result["pending"] == 2
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["basic"] == {}
        history = await manager.get_history(profile["id"])
        assert history == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_append_dedupes_and_remove_requires_evidence(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(engine)
        updates = [
            _set_update(
                section="preferences",
                field="饮品",
                value="燕麦拿铁",
                operation="append",
                evidence=[memory_id],
            ),
            _set_update(
                section="preferences",
                field="饮品",
                value="美式咖啡",
                operation="append",
                evidence=[memory_id],
            ),
            _set_update(
                section="preferences",
                field="饮品",
                value="燕麦拿铁",
                operation="append",
                evidence=[memory_id],
            ),
        ]
        await manager.apply_updates(
            SCOPE, PERSONA, updates, evidence_memory_ids=[memory_id]
        )
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["preferences"]["饮品"] == ["燕麦拿铁", "美式咖啡"]

        remove = _set_update(
            section="preferences",
            field="饮品",
            value="燕麦拿铁",
            operation="remove",
            evidence=[memory_id],
        )
        await manager.apply_updates(
            SCOPE, PERSONA, [remove], evidence_memory_ids=[memory_id]
        )
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["preferences"]["饮品"] == ["美式咖啡"]

        unproven_remove = _set_update(
            section="preferences",
            field="饮品",
            value="美式咖啡",
            operation="remove",
            evidence=[],
            confidence=0.2,
        )
        result = await manager.apply_updates(
            SCOPE, PERSONA, [unproven_remove], evidence_memory_ids=[memory_id]
        )
        assert result["pending"] == 1
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["preferences"]["饮品"] == ["美式咖啡"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_same_field_conflict_inside_batch_keeps_highest_confidence(
    tmp_path: Path,
):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(engine)
        await manager.apply_updates(
            SCOPE,
            PERSONA,
            [
                _set_update(value="低置信值", evidence=[memory_id], confidence=0.65),
                _set_update(value="高置信值", evidence=[memory_id], confidence=0.95),
            ],
            evidence_memory_ids=[memory_id],
        )
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["profile"]["basic"]["称呼"] == "高置信值"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_pending_candidate_is_replaced_by_higher_confidence(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        await manager.apply_updates(
            SCOPE, PERSONA, [_set_update(confidence=0.2)], evidence_memory_ids=[]
        )
        await manager.apply_updates(
            SCOPE, PERSONA, [_set_update(confidence=0.4)], evidence_memory_ids=[]
        )
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert len(profile["pending"]) == 1
        assert profile["pending"][0]["confidence"] == 0.4
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_scope_and_persona_rows_are_isolated(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        memory_a = await _add_evidence(engine, "A", scope="scope-a", persona="p1")
        memory_b = await _add_evidence(engine, "B", scope="scope-a", persona="p2")
        memory_c = await _add_evidence(engine, "C", scope="scope-b", persona="p1")

        await manager.apply_updates(
            "scope-a", "p1", [_set_update(value="甲", evidence=[memory_a])], [memory_a]
        )
        await manager.apply_updates(
            "scope-a", "p2", [_set_update(value="乙", evidence=[memory_b])], [memory_b]
        )
        await manager.apply_updates(
            "scope-b", "p1", [_set_update(value="丙", evidence=[memory_c])], [memory_c]
        )

        assert (await manager.get_profile("scope-a", "p1"))["profile"]["basic"][
            "称呼"
        ] == "甲"
        assert (await manager.get_profile("scope-a", "p2"))["profile"]["basic"][
            "称呼"
        ] == "乙"
        assert (await manager.get_profile("scope-b", "p1"))["profile"]["basic"][
            "称呼"
        ] == "丙"
        assert len(await manager.list_profiles("scope-a")) == 2
        assert len(await manager.list_profiles()) == 3
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_null_persona_uses_single_row(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(
            engine, "无persona记忆", scope="webchat:group:g1", persona=None
        )
        for value in ("第一次", "第二次"):
            await manager.apply_updates(
                "webchat:group:g1",
                None,
                [_set_update(value=value, evidence=[memory_id])],
                [memory_id],
            )
        profiles = await manager.list_profiles("webchat:group:g1")
        assert len(profiles) == 1
        assert profiles[0]["profile"]["basic"]["称呼"] == "第二次"
        assert profiles[0]["version"] == 2
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_history_limit_trims_old_versions(tmp_path: Path):
    engine = await _make_engine(tmp_path, profile_history_limit=2)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(engine)
        for value in ("v1", "v2", "v3"):
            await manager.apply_updates(
                SCOPE,
                PERSONA,
                [_set_update(value=value, evidence=[memory_id])],
                [memory_id],
            )
        profile = await manager.get_profile(SCOPE, PERSONA)
        assert profile["version"] == 3
        history = await manager.get_history(profile["id"])
        assert [row["version"] for row in history] == [3, 2]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_manual_patch_and_full_replace_and_delete(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(engine)
        await manager.apply_updates(
            SCOPE,
            PERSONA,
            [
                _set_update(value="小林", evidence=[memory_id]),
                _set_update(
                    section="preferences",
                    field="饮品",
                    value="茶",
                    operation="append",
                    evidence=[memory_id],
                ),
            ],
            [memory_id],
        )
        profile = await manager.get_profile(SCOPE, PERSONA)

        patched = await manager.update_profile_manual(
            profile["id"],
            patch={"basic": {"职业": "工程师"}, "preferences.饮品": "咖啡"},
        )
        assert patched["version"] == 2
        assert patched["profile"]["basic"]["职业"] == "工程师"
        assert patched["profile"]["preferences"]["饮品"] == ["咖啡"]

        replaced = await manager.update_profile_manual(
            profile["id"],
            profile_json={"status": {"当前状态": "在学习 Rust"}},
        )
        assert replaced["profile"]["basic"] == {}
        assert replaced["profile"]["status"]["当前状态"] == "在学习 Rust"
        history = await manager.get_history(profile["id"])
        assert history[0]["source"] == "manual"
        assert "status.当前状态" in history[0]["changed_fields"]

        assert await manager.delete_profile(profile["id"]) is True
        assert await manager.get_profile(SCOPE, PERSONA) is None
        async with aiosqlite.connect(engine.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM memory_profile_history WHERE profile_id = ?",
                (profile["id"],),
            )
            assert (await cursor.fetchone())[0] == 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_disabled_manager_is_noop(tmp_path: Path):
    engine = await _make_engine(tmp_path, profile_enabled=False)
    try:
        manager = engine.profile_manager
        memory_id = await _add_evidence(engine)
        result = await manager.apply_updates(
            SCOPE, PERSONA, [_set_update(evidence=[memory_id])], [memory_id]
        )
        assert result == {"applied": 0, "pending": 0, "version": None}
        assert await manager.get_profile(SCOPE, PERSONA) is None
        assert await manager.list_profiles() == []
    finally:
        await engine.close()


def test_normalize_profile_updates_validates_fields():
    updates = normalize_profile_updates(
        [
            {
                "section": "basic",
                "field": "称呼",
                "value": "小林",
                "operation": "set",
                "evidence": [3, "4", 3, 0, -1, True, "bad"],
                "confidence": 1.5,
            },
            {"section": "unknown", "field": "x", "value": "y"},
            {"section": "basic", "field": "坏字段!", "value": "y"},
            {
                "section": "basic",
                "field": "x",
                "value": "长" * 500,
            },
            {
                "section": "basic",
                "field": "x",
                "value": "y",
                "operation": "append",
            },
            {"section": "preferences", "field": "饮品", "value": "茶", "operation": "append"},
        ]
    )
    assert len(updates) == 3
    assert updates[0]["evidence"] == [3, 4]
    assert updates[0]["confidence"] == 1.0
    assert len(updates[1]["value"]) <= 200
    assert updates[1]["value"].endswith("…")
    assert updates[2]["section"] == "preferences"
    assert updates[2]["operation"] == "append"


def test_normalize_profile_updates_ignores_missing_and_garbage():
    assert normalize_profile_updates(None) == []
    assert normalize_profile_updates("nope") == []
    assert normalize_profile_updates([None, 1, "x", {}]) == []

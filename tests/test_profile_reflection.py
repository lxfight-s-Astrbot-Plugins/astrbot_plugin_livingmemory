"""反思存储任务：总结成功后才写入用户档案，失败/回滚不生效。"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.event_handler_modules.memory_reflection_storage import (
    MemoryReflectionStorageMixin,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

from .test_memory_engine import _FakeFaissDB

SCOPE = "test:private:reflect"
PERSONA = "persona-reflect"


class _Harness(MemoryReflectionStorageMixin):
    """Minimal host object exposing only the attributes _storage_task uses."""

    def __init__(self, engine, processor, config_manager, conversation_manager):
        self.memory_engine = engine
        self.memory_processor = processor
        self.config_manager = config_manager
        self.conversation_manager = conversation_manager
        self.consolidation_manager = None
        self._storage_tasks: set = set()
        self._storage_sessions_inflight: set = set()
        self._storage_session_tasks: dict = {}
        self._shutting_down = False


async def _make_engine(tmp_path: Path) -> MemoryEngine:
    engine = MemoryEngine(
        db_path=str(tmp_path / "memory.db"),
        faiss_db=_FakeFaissDB(),
        config={
            "recent_memory_count": 0,
            "atom_enabled": False,
            "graph_memory_enabled": False,
        },
    )
    await engine.initialize()
    return engine


def _conversation_manager(*, commit: bool = True):
    manager = Mock()

    async def _get_metadata(session_id, key, default=None):
        return default

    manager.get_session_metadata = AsyncMock(side_effect=_get_metadata)
    manager.commit_summary_cursor = AsyncMock(return_value=commit)
    manager.update_session_metadata = AsyncMock()
    manager.get_session_revision = AsyncMock(return_value=None)
    return manager


def _processor(metadata: dict | None = None, *, error: Exception | None = None):
    processor = Mock()
    if error is not None:
        processor.process_conversation = AsyncMock(side_effect=error)
    else:
        processor.process_conversation = AsyncMock(
            return_value=("用户介绍了自己的偏好", metadata or {}, 0.6)
        )
    processor.classify_atoms_from_metadata = Mock(return_value=[])
    return processor


_PROFILE_METADATA = {
    "topics": ["偏好"],
    "key_facts": ["用户喜欢燕麦拿铁"],
    "sentiment": "neutral",
    "profile_updates": [
        {
            "section": "preferences",
            "field": "饮品",
            "value": "燕麦拿铁",
            "operation": "append",
            "evidence": [],
            "confidence": 0.9,
        },
        {
            "section": "basic",
            "field": "称呼",
            "value": "小林",
            "operation": "set",
            "evidence": [],
            "confidence": 0.9,
        },
    ],
}


async def _run_task(harness, *, commit: bool = True, retry_count: int = 0) -> bool:
    harness.conversation_manager.commit_summary_cursor.return_value = commit
    return await harness._storage_task(
        SCOPE,
        [Mock(id=1, group_id=None, sender_id="u1", sender_name="用户")],
        PERSONA,
        0,
        1,
        retry_count,
    )


@pytest.mark.asyncio
async def test_reflection_applies_profile_updates_after_commit(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        conversation_manager = _conversation_manager()
        harness = _Harness(
            engine,
            _processor(dict(_PROFILE_METADATA)),
            ConfigManager({}),
            conversation_manager,
        )

        assert await _run_task(harness) is True

        profile = await engine.profile_manager.get_profile(SCOPE, PERSONA)
        assert profile is not None
        assert profile["profile"]["basic"]["称呼"] == "小林"
        assert profile["profile"]["preferences"]["饮品"] == ["燕麦拿铁"]
        # 档案 updates 不能污染 documents metadata（FakeFaissDB 保存内存文档）
        stored_docs = list(engine.faiss_db.docs.values())
        assert len(stored_docs) == 1
        assert "profile_updates" not in stored_docs[0]["metadata"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reflection_rollback_commit_skips_profile_updates(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        harness = _Harness(
            engine,
            _processor(dict(_PROFILE_METADATA)),
            ConfigManager({}),
            _conversation_manager(commit=False),
        )

        assert await _run_task(harness, commit=False) is False

        assert await engine.profile_manager.get_profile(SCOPE, PERSONA) is None
        async with aiosqlite.connect(engine.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            assert (await cursor.fetchone())[0] == 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reflection_llm_failure_does_not_touch_profile(tmp_path: Path):
    engine = await _make_engine(tmp_path)
    try:
        harness = _Harness(
            engine,
            _processor(error=RuntimeError("llm down")),
            ConfigManager({}),
            _conversation_manager(),
        )

        assert await _run_task(harness) is False
        assert await engine.profile_manager.get_profile(SCOPE, PERSONA) is None
    finally:
        await engine.close()

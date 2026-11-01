"""Lifecycle-resolved recall must stay authoritative through prompt formatting."""

import copy
import time

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.managers.memory_engine_crud import (
    MemoryEngineCrudMixin,
)
from astrbot_plugin_livingmemory.core.managers.memory_engine_evidence import (
    MemoryEngineEvidenceMixin,
)
from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult
from astrbot_plugin_livingmemory.core.utils import format_memories_for_injection


class _RecallEngine(MemoryEngineCrudMixin, MemoryEngineEvidenceMixin):
    """Exercise production SQL/lifecycle code without starting vector services."""

    def __init__(self, connection):
        self.db_connection = connection
        self.atom_enabled = True
        self.atom_store = object()


async def _create_atoms(connection, rows):
    await connection.execute(
        "CREATE TABLE memory_atoms (id INTEGER PRIMARY KEY, parent_memory_id INTEGER, "
        "content TEXT, status TEXT, expires_at REAL)"
    )
    await connection.executemany("INSERT INTO memory_atoms VALUES (?, ?, ?, ?, ?)", rows)
    await connection.commit()


def _parent_result():
    return HybridResult(
        doc_id=1,
        final_score=0.9,
        rrf_score=0.9,
        bm25_score=None,
        vector_score=None,
        content="STALE_TASK_A and LIVE_TASK_B",
        metadata={
            "atom_types": ["planned"],
            "summary_schema_version": "v2",
            "persona_summary": "I remember STALE_TASK_A and LIVE_TASK_B.",
            "key_facts": ["STALE_TASK_A", "LIVE_TASK_B"],
            "importance": 0.8,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["_apply_atom_policy", "_apply_agentic_atom_policy"])
@pytest.mark.parametrize("hidden_status", ["expired", "forgotten", "superseded", "active"])
async def test_partial_atom_expiry_cannot_reenter_prompt(tmp_path, policy, hidden_status):
    parent = _parent_result()
    original = copy.deepcopy(parent)
    now = time.time()
    # Even an 'active' row must disappear after its expiry time.
    hidden_expires_at = now - 3600 if hidden_status == "active" else now + 3600
    async with aiosqlite.connect(tmp_path / "atoms.db") as connection:
        await _create_atoms(
            connection,
            [
                (1, 1, "LIVE_TASK_B", "active", now + 3600),
                (2, 1, "STALE_TASK_A", hidden_status, hidden_expires_at),
            ],
        )
        cursor = await connection.execute("SELECT * FROM memory_atoms ORDER BY id")
        stored_atoms = await cursor.fetchall()
        engine = _RecallEngine(connection)
        recalled = await getattr(engine, policy)([parent])
        # The automatic recall hook passes dictionaries to the formatter.
        prompt = format_memories_for_injection(
            [{"content": result.content, "metadata": result.metadata} for result in recalled]
        )

        assert "STALE_TASK_A" not in prompt
        assert prompt.count("LIVE_TASK_B") == 1
        assert parent == original
        cursor = await connection.execute("SELECT * FROM memory_atoms ORDER BY id")
        assert await cursor.fetchall() == stored_atoms


@pytest.mark.asyncio
async def test_cached_recall_uses_current_live_facts(tmp_path):
    now = time.time()
    async with aiosqlite.connect(tmp_path / "atoms.db") as connection:
        await _create_atoms(
            connection,
            [
                (1, 1, "LIVE_TASK_B", "active", now + 3600),
                (2, 1, "LIVE_TASK_C", "active", now + 3600),
                (3, 1, "STALE_TASK_A", "expired", now - 3600),
            ],
        )
        engine = _RecallEngine(connection)
        cached = await engine._apply_atom_policy([_parent_result()])
        await connection.execute(
            "UPDATE memory_atoms SET expires_at = ? WHERE id = 1", (now - 1,)
        )
        await connection.commit()

        refreshed = await engine._apply_atom_policy(cached)
        prompt = format_memories_for_injection(refreshed)
        assert "LIVE_TASK_B" not in prompt
        assert "STALE_TASK_A" not in prompt
        assert prompt.count("LIVE_TASK_C") == 1
        assert "LIVE_TASK_B" in cached[0].content


@pytest.mark.asyncio
async def test_memory_without_atoms_preserves_personality_summary(tmp_path):
    parent = _parent_result()
    parent.metadata.pop("atom_types")
    async with aiosqlite.connect(tmp_path / "atoms.db") as connection:
        await _create_atoms(connection, [])
        recalled = await _RecallEngine(connection)._apply_atom_policy([parent])
        prompt = format_memories_for_injection(recalled)
        assert parent.metadata["persona_summary"] in prompt
        assert "Key facts: STALE_TASK_A; LIVE_TASK_B" in prompt
        assert recalled[0] is parent

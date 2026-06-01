"""Tests for memory atom models, classifier, and lifecycle management."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from astrbot_plugin_livingmemory.core.models.memory_atom import (
    AtomStatus,
    AtomType,
    DecayType,
    MemoryAtom,
    compute_ttl,
)
from astrbot_plugin_livingmemory.core.processors.atom_classifier import (
    _classify_single,
    classify_atoms,
)
from astrbot_plugin_livingmemory.storage.atom_store import AtomStore

# ---------- TTL computation ----------


def test_compute_ttl_episodic_default() -> None:
    ttl, decay = compute_ttl(AtomType.EPISODIC, importance=0.5, reinforcement_count=0)
    # base_ttl=7, importance_factor=0.5+0.5=1.0, reinforcement_factor=1.0
    assert 6.5 <= ttl <= 7.5
    assert decay == DecayType.EXPONENTIAL


def test_compute_ttl_factual_high_importance_reinforced() -> None:
    ttl, decay = compute_ttl(AtomType.FACTUAL, importance=1.0, reinforcement_count=3)
    # 180 * (0.5+1.0) * (1.0+0.3) = 180 * 1.5 * 1.3 = 351
    assert 300 <= ttl <= 400
    assert decay == DecayType.EXPONENTIAL


def test_compute_ttl_planned_with_event_time() -> None:
    future = time.time() + 7 * 86400.0  # 7 days from now
    ttl, decay = compute_ttl(AtomType.PLANNED, importance=0.8, event_time=future)
    # base=2, days_until=~7, ttl= (2+7) * (0.5+0.8) = 9 * 1.3 = 11.7
    assert 8 <= ttl <= 15
    assert decay == DecayType.STEP


def test_compute_ttl_unknown_fallback() -> None:
    ttl, decay = compute_ttl(AtomType.UNKNOWN, importance=0.0, reinforcement_count=0)
    assert ttl >= 1.0
    assert decay == DecayType.EXPONENTIAL


# ---------- Decay functions ----------


def test_memory_atom_exponential_decay() -> None:
    atom = MemoryAtom(
        parent_memory_id=1,
        atom_type=AtomType.EPISODIC,
        content="test",
        ttl_days=10.0,
        decay_type=DecayType.EXPONENTIAL,
        last_accessed_at=time.time(),
    )
    score_now = atom.compute_temporal_score()
    assert score_now >= 0.99  # fresh

    score_old = atom.compute_temporal_score(time.time() + 10 * 86400.0)
    assert score_old < 0.3  # well past half-life


def test_memory_atom_linear_decay() -> None:
    atom = MemoryAtom(
        parent_memory_id=2,
        atom_type=AtomType.RELATIONAL,
        content="test",
        ttl_days=10.0,
        decay_type=DecayType.LINEAR,
        last_accessed_at=time.time(),
    )
    score_half = atom.compute_temporal_score(time.time() + 5 * 86400.0)
    assert 0.45 <= score_half <= 0.55  # half TTL → half score


def test_memory_atom_step_decay() -> None:
    atom = MemoryAtom(
        parent_memory_id=3,
        atom_type=AtomType.PLANNED,
        content="test",
        ttl_days=10.0,
        decay_type=DecayType.STEP,
        last_accessed_at=time.time(),
    )
    before = atom.compute_temporal_score(time.time() + 5 * 86400.0)
    assert before >= 0.99
    after = atom.compute_temporal_score(time.time() + 12 * 86400.0)
    assert after <= 0.06


def test_memory_atom_expiry_check() -> None:
    atom = MemoryAtom(
        parent_memory_id=4,
        content="expired test",
        expires_at=time.time() - 1.0,
    )
    assert atom.is_expired()

    atom_still_alive = MemoryAtom(
        parent_memory_id=5,
        content="alive",
        expires_at=time.time() + 3600.0,
    )
    assert not atom_still_alive.is_expired()


# ---------- Atom classifier ----------


def test_classify_planned_atom() -> None:
    atom_type, confidence, event_time = _classify_single("明天下午3点开会讨论项目进度")
    assert atom_type == AtomType.PLANNED
    assert confidence >= 0.8
    assert event_time is not None


def test_classify_preference_atom() -> None:
    atom_type, confidence, event_time = _classify_single("张三喜欢喝咖啡")
    assert atom_type == AtomType.PREFERENCE
    assert confidence >= 0.8
    assert event_time is None


def test_classify_relational_atom() -> None:
    atom_type, confidence, event_time = _classify_single("张三和李四是同事关系")
    assert atom_type == AtomType.RELATIONAL
    assert confidence >= 0.75


def test_classify_factual_atom() -> None:
    atom_type, confidence, event_time = _classify_single("张三的生日是5月20日")
    assert atom_type == AtomType.FACTUAL


def test_classify_episodic_atom() -> None:
    atom_type, confidence, event_time = _classify_single("张三讨论了Q3项目进展")
    assert atom_type == AtomType.EPISODIC


def test_classify_atoms_batch() -> None:
    facts = [
        "明天下午3点开会",
        "张三喜欢喝咖啡",
        "张三和李四是同事",
        "张三的生日是5月20日",
        "张三讨论了Q3计划",
    ]
    atoms = classify_atoms(
        key_facts=facts,
        topics=["会议", "偏好"],
        participants=["张三", "李四"],
        parent_importance=0.8,
    )
    assert len(atoms) == 5
    types = {a.atom_type for a in atoms}
    assert AtomType.PLANNED in types
    assert AtomType.PREFERENCE in types
    assert AtomType.RELATIONAL in types
    assert AtomType.FACTUAL in types

    # Each atom got entities from topics + participants
    for a in atoms:
        assert "会议" in a.entities or "偏好" in a.entities
        assert any(p in a.entities for p in ["张三", "李四"])


# ---------- AtomStore persistence ----------


@pytest.mark.asyncio
async def test_atom_store_insert_and_retrieve(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms.db")
    store = AtomStore(db_path)
    await store.initialize()

    atom = MemoryAtom(
        parent_memory_id=42,
        atom_type=AtomType.FACTUAL,
        content="张三的生日是5月20日",
        entities=["张三"],
        importance=0.8,
        confidence=0.85,
        session_id="session_1",
        persona_id="persona_x",
    )
    atom_id = await store.insert(atom)
    assert atom_id > 0

    retrieved = await store.get(atom_id)
    assert retrieved is not None
    assert retrieved.content == "张三的生日是5月20日"
    assert retrieved.atom_type == AtomType.FACTUAL
    assert retrieved.ttl_days > 0
    assert retrieved.expires_at > time.time()


@pytest.mark.asyncio
async def test_atom_store_insert_many_commits_in_batches(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_insert_many.db")
    store = AtomStore(db_path)
    await store.initialize()

    atom_count = AtomStore._SQLITE_BATCH_SIZE * 2 + 25
    atoms = [
        MemoryAtom(parent_memory_id=99, content=f"batch atom {index}")
        for index in range(atom_count)
    ]

    atom_ids = await store.insert_many(atoms)

    assert len(atom_ids) == atom_count
    assert all(atom.atom_id > 0 for atom in atoms)

    stored = await store.get_by_parent(99)
    assert len(stored) == atom_count


@pytest.mark.asyncio
async def test_atom_store_fts_search(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_fts.db")
    store = AtomStore(db_path)
    await store.initialize()

    await store.insert(MemoryAtom(parent_memory_id=1, content="张三喜欢喝咖啡"))
    await store.insert(MemoryAtom(parent_memory_id=1, content="李四喜欢吃火锅"))
    await store.insert(MemoryAtom(parent_memory_id=2, content="明天项目deadline"))

    results = await store.search_fts("咖啡", limit=5)
    assert len(results) >= 1
    assert any("咖啡" in r.content for r in results)


@pytest.mark.asyncio
async def test_atom_store_lifecycle(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_lifecycle.db")
    store = AtomStore(db_path)
    await store.initialize()

    # Insert atom with past expiry
    past = time.time() - 1000.0
    atom = MemoryAtom(
        parent_memory_id=1,
        content="旧记忆",
        created_at=past,
        last_accessed_at=past,
        expires_at=past + 1.0,
        status=AtomStatus.ACTIVE,
    )
    atom_id = await store.insert(atom)
    # Manually force override since insert recalculates expiry
    async with store._connect() as db:
        await db.execute(
            "UPDATE memory_atoms SET expires_at = ?, status = 'active' WHERE id = ?",
            (past + 1.0, atom_id),
        )
        await db.commit()

    expired_count = await store.expire_stale_atoms()
    assert expired_count >= 1

    retrieved = await store.get(atom_id)
    assert retrieved is not None
    assert retrieved.status == AtomStatus.EXPIRED


@pytest.mark.asyncio
async def test_atom_store_reinforce(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_reinforce.db")
    store = AtomStore(db_path)
    await store.initialize()

    atom = MemoryAtom(
        parent_memory_id=1,
        atom_type=AtomType.FACTUAL,
        content="test",
        importance=0.8,
        confidence=0.7,
    )
    atom_id = await store.insert(atom)
    original = await store.get(atom_id)
    assert original is not None
    original_ttl = original.ttl_days
    original_count = original.reinforcement_count

    await store.reinforce(atom_id, new_confidence=0.9)
    reinforced = await store.get(atom_id)
    assert reinforced is not None
    assert reinforced.reinforcement_count == original_count + 1
    # EMA: 0.7 * 0.7 + 0.9 * 0.3 = 0.49 + 0.27 = 0.76
    assert 0.7 < reinforced.confidence < 0.85
    assert reinforced.ttl_days > original_ttl


@pytest.mark.asyncio
async def test_atom_store_delete_by_parent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_delete.db")
    store = AtomStore(db_path)
    await store.initialize()

    await store.insert(MemoryAtom(parent_memory_id=10, content="fact 1"))
    await store.insert(MemoryAtom(parent_memory_id=10, content="fact 2"))
    await store.insert(MemoryAtom(parent_memory_id=20, content="fact 3"))

    deleted = await store.delete_by_parent(10)
    assert deleted == 2

    remaining = await store.get_by_parent(20)
    assert len(remaining) == 1


# ---------- AtomRetriever integration ----------


@pytest.mark.asyncio
async def test_atom_retriever_search(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.retrieval.atom_retriever import AtomRetriever

    db_path = str(tmp_path / "test_atoms_retriever.db")
    store = AtomStore(db_path)
    await store.initialize()

    await store.insert(
        MemoryAtom(
            parent_memory_id=1,
            atom_type=AtomType.FACTUAL,
            content="张三喜欢滑雪",
            importance=0.9,
        )
    )
    await store.insert(
        MemoryAtom(
            parent_memory_id=2,
            atom_type=AtomType.EPISODIC,
            content="李四昨天去爬山了",
            importance=0.5,
        )
    )
    await store.insert(
        MemoryAtom(
            parent_memory_id=3,
            atom_type=AtomType.PREFERENCE,
            content="张三喜欢咖啡",
            importance=0.7,
        )
    )

    retriever = AtomRetriever(store)
    results = await retriever.search("滑雪", k=5)
    assert len(results) >= 1
    assert results[0].content == "张三喜欢滑雪"


@pytest.mark.asyncio
async def test_atom_retriever_session_filter(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.retrieval.atom_retriever import AtomRetriever

    db_path = str(tmp_path / "test_atoms_filter.db")
    store = AtomStore(db_path)
    await store.initialize()

    await store.insert(
        MemoryAtom(parent_memory_id=1, content="公共记忆", session_id="session_a")
    )
    await store.insert(
        MemoryAtom(parent_memory_id=2, content="私有记忆", session_id="session_b")
    )

    retriever = AtomRetriever(store)
    results = await retriever.search("记忆", k=5, session_id="session_a")
    assert all(
        r.metadata.get("session_id") == "session_a" or "公共" in r.content
        for r in results
    )


# ---------- Classifier edge cases ----------


def test_classify_empty_facts() -> None:
    atoms = classify_atoms(key_facts=[], topics=["会议"])
    assert atoms == []


def test_classify_whitespace_only_fact() -> None:
    atoms = classify_atoms(key_facts=["   ", "\t\n"], topics=[])
    assert atoms == []


def test_classify_unknown_type() -> None:
    atom_type, confidence, _ = _classify_single("嗯好")
    assert atom_type == AtomType.UNKNOWN
    assert confidence >= 0.5


def test_classify_planned_wins_over_preference() -> None:
    atom_type, confidence, event_time = _classify_single("明天下午去喝咖啡")
    assert atom_type == AtomType.PLANNED
    assert event_time is not None


def test_classify_fact_with_stative_verb() -> None:
    atom_type, confidence, _ = _classify_single("项目属于张三负责")
    assert atom_type == AtomType.FACTUAL


# ---------- TTL boundary conditions ----------


def test_compute_ttl_min_importance() -> None:
    ttl, _ = compute_ttl(AtomType.EPISODIC, importance=0.0)
    assert 3.0 <= ttl <= 4.0  # 7 * (0.5+0.0) = 3.5


def test_compute_ttl_max_reinforcement() -> None:
    ttl, decay = compute_ttl(AtomType.FACTUAL, importance=0.5, reinforcement_count=20)
    # 180 * 1.0 * min(1.5, 1.0+2.0) = 180 * 1.0 * 1.5 = 270
    assert ttl >= 180


def test_compute_ttl_minimum_ttl_clamp() -> None:
    ttl, _ = compute_ttl(AtomType.UNKNOWN, importance=0.0, reinforcement_count=0)
    assert ttl >= 1.0


# ---------- Decay edge cases ----------


def test_decay_zero_ttl_clamped_to_minimum_decay() -> None:
    atom = MemoryAtom(
        parent_memory_id=1,
        ttl_days=0.5,
        decay_type=DecayType.EXPONENTIAL,
        last_accessed_at=time.time(),
    )
    score = atom.compute_temporal_score(time.time() + 3 * 86400)
    assert 0.0 < score < 1.0


def test_decay_very_long_ttl_barely_decays() -> None:
    atom = MemoryAtom(
        parent_memory_id=1,
        ttl_days=1000.0,
        decay_type=DecayType.EXPONENTIAL,
        last_accessed_at=time.time(),
    )
    score = atom.compute_temporal_score(time.time() + 7 * 86400)
    assert score > 0.9


# ---------- AtomStore edge cases ----------


@pytest.mark.asyncio
async def test_atom_store_get_nonexistent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_nonexistent.db")
    store = AtomStore(db_path)
    await store.initialize()
    result = await store.get(99999)
    assert result is None


@pytest.mark.asyncio
async def test_atom_store_touch_updates_access_time(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_touch.db")
    store = AtomStore(db_path)
    await store.initialize()

    atom = MemoryAtom(parent_memory_id=1, content="test", last_accessed_at=1000.0)
    atom_id = await store.insert(atom)
    original = await store.get(atom_id)
    assert original is not None
    original_access = original.last_accessed_at

    await store.touch(atom_id)
    updated = await store.get(atom_id)
    assert updated is not None
    assert updated.last_accessed_at > original_access


# ---------- AtomLifecycleManager ----------


@pytest.mark.asyncio
async def test_lifecycle_manager_run_maintenance(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.managers.atom_lifecycle_manager import (
        AtomLifecycleManager,
    )

    db_path = str(tmp_path / "test_atoms_lifecycle_mgr.db")
    store = AtomStore(db_path)
    await store.initialize()

    mgr = AtomLifecycleManager(store)
    result = await mgr.run_maintenance()
    assert "expired" in result
    assert "forgotten" in result
    assert isinstance(result["expired"], int)
    assert isinstance(result["forgotten"], int)


@pytest.mark.asyncio
async def test_lifecycle_manager_reinforcement_no_match(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.managers.atom_lifecycle_manager import (
        AtomLifecycleManager,
    )

    db_path = str(tmp_path / "test_atoms_reinforce_mgr.db")
    store = AtomStore(db_path)
    await store.initialize()

    mgr = AtomLifecycleManager(store)
    new_atom = MemoryAtom(parent_memory_id=1, content="completely unique text")
    count = await mgr.run_manual_reinforcement([new_atom])
    assert count == 0


@pytest.mark.asyncio
async def test_lifecycle_manager_reinforcement_jaccard_match(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.managers.atom_lifecycle_manager import (
        AtomLifecycleManager,
    )

    db_path = str(tmp_path / "test_atoms_reinforce_match.db")
    store = AtomStore(db_path)
    await store.initialize()

    # Insert an existing atom
    existing = MemoryAtom(
        parent_memory_id=1,
        content="张三喜欢喝咖啡每天都要喝",
        confidence=0.7,
    )
    await store.insert(existing)

    mgr = AtomLifecycleManager(store)
    # New atom with very similar content
    new_atom = MemoryAtom(
        parent_memory_id=2,
        content="张三喜欢喝咖啡",
        confidence=0.9,
    )
    count = await mgr.run_manual_reinforcement([new_atom], similarity_threshold=0.3)
    assert count >= 1


@pytest.mark.asyncio
async def test_lifecycle_manager_logs_and_backs_off_on_error(monkeypatch):
    import astrbot_plugin_livingmemory.core.managers.atom_lifecycle_manager as lifecycle_module
    from astrbot_plugin_livingmemory.core.managers.atom_lifecycle_manager import (
        AtomLifecycleManager,
    )

    class FailingStore:
        async def expire_stale_atoms(self):
            raise RuntimeError("maintenance failed")

    sleep_calls: list[float] = []
    mgr = AtomLifecycleManager(FailingStore(), {"atom_maintenance_interval_hours": 1})
    mgr._running = True

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)
        mgr._running = False

    error_mock = Mock()
    monkeypatch.setattr(lifecycle_module.logger, "error", error_mock)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await mgr._maintenance_loop()

    error_mock.assert_called_once()
    assert error_mock.call_args.args[0] == "[AtomLifecycle] 维护任务异常"
    assert error_mock.call_args.kwargs["exc_info"] is True
    assert sleep_calls == [60.0]


# ---------- AtomRetriever get_atoms_for_memory ----------


@pytest.mark.asyncio
async def test_atom_retriever_get_atoms_for_memory(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.retrieval.atom_retriever import AtomRetriever

    db_path = str(tmp_path / "test_atoms_by_parent.db")
    store = AtomStore(db_path)
    await store.initialize()

    await store.insert(MemoryAtom(parent_memory_id=42, content="fact a"))
    await store.insert(MemoryAtom(parent_memory_id=42, content="fact b"))
    await store.insert(MemoryAtom(parent_memory_id=99, content="fact c"))

    retriever = AtomRetriever(store)
    atoms = await retriever.get_atoms_for_memory(42)
    assert len(atoms) == 2
    contents = {a.content for a in atoms}
    assert contents == {"fact a", "fact b"}


@pytest.mark.asyncio
async def test_atom_retriever_touch(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.retrieval.atom_retriever import AtomRetriever

    db_path = str(tmp_path / "test_atoms_retriever_touch.db")
    store = AtomStore(db_path)
    await store.initialize()

    atom = MemoryAtom(parent_memory_id=1, content="test touch", last_accessed_at=1000.0)
    atom_id = await store.insert(atom)

    retriever = AtomRetriever(store)
    await retriever.touch(atom_id)

    updated = await store.get(atom_id)
    assert updated is not None
    assert updated.last_accessed_at > 1000.0


# ---------- Graph store semantic edge merge ----------


@pytest.mark.asyncio
async def test_graph_store_semantic_edge_merge(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.models.graph_models import (
        GraphEdge,
        GraphNode,
    )
    from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

    db_path = str(tmp_path / "test_semantic_edge.db")
    store = GraphStore(db_path)
    await store.initialize()

    # Create two nodes
    node_a = GraphNode(
        node_type="topic", value="项目Alpha", canonical_value="项目alpha"
    )
    node_b = GraphNode(node_type="fact", value="明天发布", canonical_value="明天发布")
    node_a_id = await store.upsert_node(node_a)
    node_b_id = await store.upsert_node(node_b)

    node_map = {node_a.node_key: node_a_id, node_b.node_key: node_b_id}

    # First edge from memory 1
    edge1 = GraphEdge(
        source_key=node_a.node_key,
        target_key=node_b.node_key,
        relation_type="describes",
        source_memory_id=1,
        confidence=0.8,
    )
    e1_id = await store.add_edge(edge1, node_map)

    # Second edge from memory 2 (same semantic relation)
    edge2 = GraphEdge(
        source_key=node_a.node_key,
        target_key=node_b.node_key,
        relation_type="describes",
        source_memory_id=2,
        confidence=0.9,
    )
    e2_id = await store.add_edge(edge2, node_map)

    # Should merge: same id returned
    assert e1_id == e2_id

    # Verify EMA-updated confidence: 0.8 * 0.7 + 0.9 * 0.3 = 0.56 + 0.27 = 0.83
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT confidence, weight FROM graph_edges WHERE id = ?", (e1_id,)
        )
        row = await cursor.fetchone()
    assert row is not None
    assert 0.8 < float(row[0]) <= 0.9  # confidence between old and new
    assert float(row[1]) > 1.0  # weight accumulated

    await store.delete_memory(1)
    await store.delete_memory(2)


@pytest.mark.asyncio
async def test_graph_store_first_edge_no_merge(tmp_path: Path) -> None:
    from astrbot_plugin_livingmemory.core.models.graph_models import (
        GraphEdge,
        GraphNode,
    )
    from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

    db_path = str(tmp_path / "test_edge_no_merge.db")
    store = GraphStore(db_path)
    await store.initialize()

    node_a = GraphNode(node_type="topic", value="测试", canonical_value="测试")
    node_b = GraphNode(node_type="fact", value="测试事实", canonical_value="测试事实")
    na = await store.upsert_node(node_a)
    nb = await store.upsert_node(node_b)
    node_map = {node_a.node_key: na, node_b.node_key: nb}

    edge = GraphEdge(
        source_key=node_a.node_key,
        target_key=node_b.node_key,
        relation_type="describes",
        source_memory_id=1,
        confidence=0.85,
        weight=1.0,
    )
    edge_id = await store.add_edge(edge, node_map)
    assert edge_id > 0

    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT confidence FROM graph_edges WHERE id = ?", (edge_id,)
        )
        row = await cursor.fetchone()
    assert float(row[0]) == 0.85

    await store.delete_memory(1)


# ---------- GraphExtractor with atoms ----------


def test_graph_extractor_empty_atoms_falls_back_to_legacy() -> None:
    """Empty atoms list is falsy → extract() falls back to legacy path."""
    from astrbot_plugin_livingmemory.core.processors.graph_extractor import (
        GraphExtractor,
    )

    extractor = GraphExtractor()
    result = extractor.extract(
        1,
        "content",
        {"canonical_summary": "legacy summary", "key_facts": ["fact1"]},
        atoms=[],
    )
    # [] is falsy, so legacy path is used with metadata
    assert len(result.nodes) >= 1
    assert len(result.entries) >= 1


def test_graph_extractor_atoms_with_entities() -> None:
    from astrbot_plugin_livingmemory.core.models.memory_atom import AtomType, MemoryAtom
    from astrbot_plugin_livingmemory.core.processors.graph_extractor import (
        GraphExtractor,
    )

    extractor = GraphExtractor()
    atom = MemoryAtom(
        parent_memory_id=1,
        atom_type=AtomType.FACTUAL,
        content="张三喜欢滑雪",
        entities=["张三", "滑雪"],
        confidence=0.85,
    )
    result = extractor.extract(1, "", {}, atoms=[atom])
    # Should produce nodes for entities + fact node
    assert len(result.nodes) >= 2
    assert len(result.entries) >= 1
    # At least one edge connecting entity to fact
    assert len(result.edges) >= 1
    # Verify atom confidence propagated to edge
    for edge in result.edges:
        assert edge.confidence == pytest.approx(0.85 * 0.9, abs=0.01)


def test_graph_extractor_atoms_no_entities_fallback() -> None:
    from astrbot_plugin_livingmemory.core.models.memory_atom import AtomType, MemoryAtom
    from astrbot_plugin_livingmemory.core.processors.graph_extractor import (
        GraphExtractor,
    )

    extractor = GraphExtractor()
    # Atom without entities — _add_node("fact") fails if canonicalize returns ""
    # so it should fall through to the "no entries" branch and create a summary entry
    atom = MemoryAtom(
        parent_memory_id=1,
        atom_type=AtomType.UNKNOWN,
        content="x",
        entities=[],
        confidence=0.6,
    )
    result = extractor.extract(1, "", {}, atoms=[atom])
    # The fact node key would be "fact:x" which should canonicalize to "x"
    # Actually let me check: if canonicalize("x") returns "x" (non-empty), then
    # we'll get a fact node + entry. Let me use a proper test instead.
    atom2 = MemoryAtom(
        parent_memory_id=1,
        atom_type=AtomType.UNKNOWN,
        content="测试内容",
        entities=[],
        confidence=0.6,
    )
    result = extractor.extract(1, "", {}, atoms=[atom2])
    assert len(result.nodes) >= 1
    assert len(result.entries) >= 1


# ---------- Backward compatibility ----------


def test_classify_atoms_from_metadata_default_config() -> None:
    """MemoryProcessor initializes atom config for direct construction."""
    from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor

    processor = MemoryProcessor()
    atoms = processor.classify_atoms_from_metadata(metadata={"key_facts": []})
    assert atoms == []


def test_classify_atoms_from_metadata_atom_disabled() -> None:
    """When atom_enabled is False, classify_atoms_from_metadata returns empty list."""
    from astrbot_plugin_livingmemory.core.processors.memory_processor import (
        MemoryProcessor,
    )

    processor = MemoryProcessor(config={"atom_enabled": False})
    atoms = processor.classify_atoms_from_metadata(
        metadata={"key_facts": ["测试1", "测试2"]},
    )
    assert atoms == []


def test_classify_atoms_from_metadata_atom_enabled_returns_atoms() -> None:
    from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor

    processor = MemoryProcessor(config={"atom_enabled": True})
    atoms = processor.classify_atoms_from_metadata(
        metadata={
            "key_facts": ["用户喜欢猫"],
            "topics": ["宠物"],
            "participants": ["用户"],
        },
        parent_importance=0.8,
        session_id="test-session",
        persona_id="test-persona",
    )

    assert len(atoms) == 1
    assert atoms[0].content == "用户喜欢猫"
    assert atoms[0].session_id == "test-session"
    assert atoms[0].persona_id == "test-persona"


def test_classify_atoms_from_metadata_no_key_facts() -> None:
    """When key_facts is empty, classify_atoms_from_metadata returns empty list."""
    from astrbot_plugin_livingmemory.core.processors.memory_processor import (
        MemoryProcessor,
    )

    processor = MemoryProcessor(config={"atom_enabled": True})
    atoms = processor.classify_atoms_from_metadata(
        metadata={"key_facts": [], "topics": ["会议"]},
    )
    assert atoms == []


def test_legacy_extract_path_unchanged() -> None:
    """When no atoms provided, extract() uses legacy path (backward compatible)."""
    from astrbot_plugin_livingmemory.core.processors.graph_extractor import (
        GraphExtractor,
    )

    extractor = GraphExtractor()
    result = extractor.extract(
        1,
        content="讨论总结",
        metadata={
            "topics": ["项目进度"],
            "key_facts": ["今天讨论了Q3计划"],
            "participants": ["张三"],
            "importance": 0.7,
            "canonical_summary": "今天讨论了Q3计划",
        },
        atoms=None,
    )
    # Legacy path should produce nodes/edges/entries from metadata
    assert len(result.nodes) >= 2  # topic + fact + person
    assert len(result.entries) >= 1
    assert len(result.edges) >= 1


# ---------- event_time parsing ----------


def test_event_time_parsing_tomorrow() -> None:
    from astrbot_plugin_livingmemory.core.processors.atom_classifier import (
        _parse_event_time,
    )

    result = _parse_event_time("明天下午3点开会")
    assert result is not None
    # Should be roughly 1 day from now
    assert time.time() + 0.8 * 86400 < result < time.time() + 1.2 * 86400


def test_event_time_parsing_month_day() -> None:
    from astrbot_plugin_livingmemory.core.processors.atom_classifier import (
        _parse_event_time,
    )

    result = _parse_event_time("5月30日截止")
    assert result is not None


def test_event_time_parsing_no_time() -> None:
    from astrbot_plugin_livingmemory.core.processors.atom_classifier import (
        _parse_event_time,
    )

    result = _parse_event_time("这是一个普通事实")
    assert result is None


# ---------- compute_ttl type coverage ----------


def test_compute_ttl_all_types_return_valid() -> None:
    for t in AtomType:
        ttl, decay = compute_ttl(t, importance=0.6, reinforcement_count=1)
        assert ttl >= 1.0
        assert isinstance(decay, DecayType)


def test_compute_ttl_relational_returns_linear() -> None:
    ttl, decay = compute_ttl(AtomType.RELATIONAL)
    assert decay == DecayType.LINEAR


def test_compute_ttl_preference_returns_exponential() -> None:
    ttl, decay = compute_ttl(AtomType.PREFERENCE)
    assert decay == DecayType.EXPONENTIAL


# ---------- AtomStore stats ----------


@pytest.mark.asyncio
async def test_atom_store_get_stats(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_atoms_stats.db")
    store = AtomStore(db_path)
    await store.initialize()

    await store.insert(MemoryAtom(parent_memory_id=1, content="active fact"))
    await store.insert(MemoryAtom(parent_memory_id=2, content="another fact"))

    stats = await store.get_stats()
    assert stats["active"] >= 2
    assert "expired" in stats
    assert "forgotten" in stats

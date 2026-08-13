"""Tests for memory consolidation manager and merge-memory processor."""

from unittest.mock import AsyncMock, Mock

import pytest

from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.managers.consolidation_manager import (
    MemoryConsolidationManager,
)
from astrbot_plugin_livingmemory.core.processors.memory_processor import MemoryProcessor


def _make_cfg(**overrides) -> ConfigManager:
    base = {
        "memory_consolidation": {
            "enabled": True,
            "trigger": "daily",
            "granularity": "session",
            "keep_original": "archive",
            "min_memories_per_group": 2,
            "min_age_days": 7,
            "max_importance": 0.5,
            "max_groups_per_run": 5,
            "semantic_similarity_threshold": 0.7,
        }
    }
    base["memory_consolidation"].update(overrides)
    return ConfigManager(base)


def _make_manager(config_manager, engine=None, processor=None):
    return MemoryConsolidationManager(
        engine or Mock(), processor or Mock(), config_manager
    )


class TestGroupBySession:
    def test_groups_by_session_id(self):
        mgr = _make_manager(_make_cfg())
        candidates = [
            {"id": 1, "metadata": {"session_id": "s1"}},
            {"id": 2, "metadata": {"session_id": "s1"}},
            {"id": 3, "metadata": {"session_id": "s2"}},
        ]
        groups = mgr._group_by_session(candidates)
        by_size = sorted([len(g) for g in groups], reverse=True)
        assert by_size == [2, 1]

    def test_skips_sessionless_memories(self):
        mgr = _make_manager(_make_cfg())
        candidates = [
            {"id": 1, "metadata": {}},
            {"id": 2, "metadata": {"session_id": None}},
            {"id": 3, "metadata": {"session_id": "s1"}},
        ]
        groups = mgr._group_by_session(candidates)
        assert len(groups) == 1
        assert len(groups[0]) == 1


class TestBuildGroups:
    @pytest.mark.asyncio
    async def test_filters_below_min_size_and_sorts_desc(self):
        mgr = _make_manager(_make_cfg(min_memories_per_group=3))
        candidates = [
            {"id": i, "metadata": {"session_id": f"s{i % 4}"}} for i in range(1, 13)
        ]
        groups = await mgr._build_groups(candidates, mgr.config)
        assert groups
        sizes = [len(g) for g in groups]
        assert sizes == sorted(sizes, reverse=True)
        assert all(s >= 3 for s in sizes)


class TestRunConsolidation:
    @pytest.mark.asyncio
    async def test_disabled_skips(self):
        mgr = _make_manager(_make_cfg(enabled=False))
        result = await mgr.run_consolidation(force=True)
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_merges_and_archives(self):
        config = _make_cfg(keep_original="archive")
        engine = Mock()
        processor = Mock()
        mgr = _make_manager(config, engine=engine, processor=processor)

        group = [
            {"id": 1, "content": "a", "metadata": {"session_id": "s1"}},
            {"id": 2, "content": "b", "metadata": {"session_id": "s1"}},
        ]
        mgr._query_candidates = AsyncMock(return_value=group)
        mgr._build_groups = AsyncMock(return_value=[group])
        processor.merge_memories = AsyncMock(
            return_value={
                "summary": "merged",
                "key_facts": ["f1"],
                "topics": ["t1"],
                "importance": 0.4,
            }
        )
        engine.add_memory = AsyncMock(return_value=100)
        engine.archive_memories = AsyncMock(return_value=2)

        result = await mgr.run_consolidation(force=True)

        assert result["merged"] == 2
        assert result["archived"] == 2
        processor.merge_memories.assert_awaited_once_with(group)
        engine.add_memory.assert_awaited_once()
        engine.archive_memories.assert_awaited_once_with([1, 2])

    @pytest.mark.asyncio
    async def test_delete_keep_original(self):
        config = _make_cfg(keep_original="delete")
        engine = Mock()
        processor = Mock()
        mgr = _make_manager(config, engine=engine, processor=processor)

        group = [{"id": 5, "content": "x", "metadata": {"session_id": "s1"}}]
        mgr._query_candidates = AsyncMock(return_value=group)
        mgr._build_groups = AsyncMock(return_value=[group])
        processor.merge_memories = AsyncMock(
            return_value={
                "summary": "merged",
                "key_facts": [],
                "topics": [],
                "importance": 0.4,
            }
        )
        engine.add_memory = AsyncMock(return_value=200)
        engine.batch_delete_memories = AsyncMock(return_value=1)

        result = await mgr.run_consolidation(force=True)

        assert result["deleted"] == 1
        engine.batch_delete_memories.assert_awaited_once_with([5])

    @pytest.mark.asyncio
    async def test_merge_failure_increments_failed(self):
        config = _make_cfg()
        engine = Mock()
        processor = Mock()
        mgr = _make_manager(config, engine=engine, processor=processor)

        group = [{"id": 1, "content": "x", "metadata": {"session_id": "s1"}}]
        mgr._query_candidates = AsyncMock(return_value=group)
        mgr._build_groups = AsyncMock(return_value=[group])
        processor.merge_memories = AsyncMock(side_effect=RuntimeError("boom"))

        result = await mgr.run_consolidation(force=True)

        assert result["failed"] == 1
        assert result["merged"] == 0


class TestGroupSemantic:
    @pytest.mark.asyncio
    async def test_union_by_similarity(self):
        config = _make_cfg(granularity="semantic")
        engine = Mock()
        engine.vector_retriever = Mock()
        engine.vector_retriever.find_similar_pairs = AsyncMock(
            return_value=[(1, 2, 0.9)]
        )
        mgr = _make_manager(config, engine=engine)

        candidates = [
            {"id": 1, "content": "a", "metadata": {}},
            {"id": 2, "content": "b", "metadata": {}},
            {"id": 3, "content": "c", "metadata": {}},
        ]

        groups = await mgr._group_semantic(candidates, mgr.config)

        assert {len(g) for g in groups} == {2, 1}

    @pytest.mark.asyncio
    async def test_falls_back_to_session_when_no_vector_retriever(self):
        config = _make_cfg(granularity="semantic")
        engine = Mock()
        engine.vector_retriever = None
        mgr = _make_manager(config, engine=engine)

        candidates = [
            {"id": 1, "content": "a", "metadata": {"session_id": "s1"}},
            {"id": 2, "content": "b", "metadata": {"session_id": "s1"}},
            {"id": 3, "content": "c", "metadata": {"session_id": "s2"}},
        ]

        groups = await mgr._group_semantic(candidates, mgr.config)

        assert {len(g) for g in groups} == {2, 1}

    @pytest.mark.asyncio
    async def test_falls_back_on_clustering_error(self):
        config = _make_cfg(granularity="semantic")
        engine = Mock()
        engine.vector_retriever = Mock()
        engine.vector_retriever.find_similar_pairs = AsyncMock(
            side_effect=RuntimeError("faiss unavailable")
        )
        mgr = _make_manager(config, engine=engine)

        candidates = [
            {"id": 1, "content": "a", "metadata": {"session_id": "s1"}},
            {"id": 2, "content": "b", "metadata": {"session_id": "s1"}},
        ]

        groups = await mgr._group_semantic(candidates, mgr.config)

        assert [len(g) for g in groups] == [2]


class TestVectorRetrieverSimilarPairs:
    @pytest.mark.asyncio
    async def test_find_similar_pairs_thresholds_and_dedupes(self):
        import numpy as np

        from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import (
            VectorRetriever,
        )

        class _Idx:
            def __init__(self):
                self.vecs = {
                    1: np.array([1.0, 0.0], dtype=np.float32),
                    2: np.array([1.0, 0.1], dtype=np.float32),
                    3: np.array([0.0, 1.0], dtype=np.float32),
                }

            def reconstruct(self, doc_id):
                return self.vecs[doc_id]

            def search(self, matrix, k):
                ids = list(self.vecs.keys())
                all_vecs = np.stack([self.vecs[i] for i in ids]).astype("float32")
                diff = matrix[:, None, :] - all_vecs[None, :, :]
                dist = (diff**2).sum(-1)
                order = np.argsort(dist, axis=1)[:, :k]
                d = np.take_along_axis(dist, order, axis=1)
                i = np.array(ids)[order]
                return d.astype("float32"), i.astype("int64")

        class _Storage:
            def __init__(self):
                self.index = _Idx()

        class _FaissDB:
            def __init__(self):
                self.embedding_storage = _Storage()

        retriever = VectorRetriever(_FaissDB())
        pairs = await retriever.find_similar_pairs([1, 2, 3], threshold=0.9, k=2)

        sims = {tuple(sorted((a, b))): s for a, b, s in pairs}
        assert (1, 2) in sims
        assert sims[(1, 2)] >= 0.9
        assert (1, 3) not in sims
        assert (2, 3) not in sims

    @pytest.mark.asyncio
    async def test_find_similar_pairs_skips_missing_vectors(self):
        from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import (
            VectorRetriever,
        )

        index = Mock()
        index.reconstruct = Mock(
            side_effect=lambda doc_id: (_ for _ in ()).throw(RuntimeError("missing"))
        )

        class _Storage:
            def __init__(self):
                self.index = index

        class _FaissDB:
            def __init__(self):
                self.embedding_storage = _Storage()

        retriever = VectorRetriever(_FaissDB())
        pairs = await retriever.find_similar_pairs([1, 2], threshold=0.9)

        assert pairs == []


class TestMemoryProcessorMerge:
    def test_parse_merge_response_plain_json(self):
        processor = MemoryProcessor(llm_provider=Mock(), context=None)
        data = processor._parse_merge_response(
            '{"summary": "s", "key_facts": ["a"], "topics": ["t"], "importance": 0.6}'
        )
        assert data["summary"] == "s"
        assert data["importance"] == 0.6

    def test_parse_merge_response_markdown_fence(self):
        processor = MemoryProcessor(llm_provider=Mock(), context=None)
        data = processor._parse_merge_response(
            '```json\n{"summary": "s", "key_facts": [], "topics": []}\n```'
        )
        assert data["summary"] == "s"

    def test_parse_merge_response_invalid_raises(self):
        processor = MemoryProcessor(llm_provider=Mock(), context=None)
        with pytest.raises(RuntimeError):
            processor._parse_merge_response("not json at all")

    @pytest.mark.asyncio
    async def test_merge_memories_builds_result(self):
        processor = MemoryProcessor(llm_provider=Mock(), context=None)
        processor._call_llm_with_retry = AsyncMock(
            return_value='{"summary": "合并摘要", "key_facts": ["f1", "f2"], '
            '"topics": ["t1"], "importance": 0.55}'
        )

        result = await processor.merge_memories(
            [
                {"content": "x", "metadata": {"persona_summary": "记忆一"}},
                {"content": "y", "metadata": {"persona_summary": "记忆二"}},
            ]
        )

        assert result["summary"] == "合并摘要"
        assert result["key_facts"] == ["f1", "f2"]
        assert result["topics"] == ["t1"]
        assert result["importance"] == 0.55

    @pytest.mark.asyncio
    async def test_merge_memories_empty_summary_raises(self):
        processor = MemoryProcessor(llm_provider=Mock(), context=None)
        processor._call_llm_with_retry = AsyncMock(
            return_value='{"summary": "", "key_facts": []}'
        )

        with pytest.raises(RuntimeError):
            await processor.merge_memories([{"content": "x", "metadata": {}}])

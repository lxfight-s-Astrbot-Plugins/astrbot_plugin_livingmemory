"""Tests for graph-memory indexing and dual-route retrieval."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
from astrbot_plugin_livingmemory.core.processors.graph_extractor import GraphExtractor
from astrbot_plugin_livingmemory.core.processors.text_processor import TextProcessor
from astrbot_plugin_livingmemory.core.retrieval.graph_keyword_retriever import (
    GraphKeywordRetriever,
)
from astrbot_plugin_livingmemory.core.retrieval.graph_retriever import GraphRetriever
from astrbot_plugin_livingmemory.core.retrieval.graph_vector_retriever import (
    GraphVectorRetriever,
)
from astrbot_plugin_livingmemory.core.retrieval.rrf_fusion import RRFFusion
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore
from astrbot_plugin_livingmemory.core.managers.graph_memory_manager import (
    GraphMemoryManager,
)


@dataclass
class _FakeRetrieveResult:
    similarity: float
    data: dict


class _FakeDocumentStorage:
    def __init__(self, db: "_FakeFaissDB"):
        self._db = db

    async def get_documents(self, metadata_filters, ids=None, limit=50, offset=0):
        docs = list(self._db.docs.values())
        if ids is not None:
            id_set = set(ids)
            docs = [doc for doc in docs if doc["id"] in id_set]

        for key, value in (metadata_filters or {}).items():
            docs = [doc for doc in docs if doc["metadata"].get(key) == value]

        docs = docs[offset : offset + limit]
        return [dict(doc) for doc in docs]

    async def count_documents(self, metadata_filters):
        docs = list(self._db.docs.values())
        for key, value in (metadata_filters or {}).items():
            docs = [doc for doc in docs if doc["metadata"].get(key) == value]
        return len(docs)


class _FakeFaissDB:
    def __init__(self):
        self.docs: dict[int, dict] = {}
        self._next_id = 1
        self.document_storage = _FakeDocumentStorage(self)

    async def insert(self, content: str, metadata: dict) -> int:
        doc_id = self._next_id
        self._next_id += 1
        self.docs[doc_id] = {
            "id": doc_id,
            "doc_id": f"uuid-{doc_id}",
            "text": content,
            "metadata": dict(metadata),
        }
        return doc_id

    async def retrieve(
        self, query: str, k: int, fetch_k: int, rerank: bool, metadata_filters=None
    ):
        results: list[_FakeRetrieveResult] = []
        for doc in self.docs.values():
            if metadata_filters:
                matched = True
                for key, value in metadata_filters.items():
                    if doc["metadata"].get(key) != value:
                        matched = False
                        break
                if not matched:
                    continue

            text = doc["text"]
            score = 0.9 if query in text else 0.2
            results.append(
                _FakeRetrieveResult(
                    similarity=score,
                    data={
                        "id": doc["id"],
                        "text": text,
                        "metadata": dict(doc["metadata"]),
                    },
                )
            )

        results.sort(key=lambda item: item.similarity, reverse=True)
        return results[:k]

    async def delete(self, uuid_doc_id: str) -> None:
        target = None
        for doc_id, doc in self.docs.items():
            if doc["doc_id"] == uuid_doc_id:
                target = doc_id
                break
        if target is not None:
            self.docs.pop(target, None)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_graph_memory_manager_indexes_nodes_edges_and_entries(tmp_path: Path):
    db_path = tmp_path / "graph_memory.db"
    graph_store = GraphStore(str(db_path))
    await graph_store.initialize()

    graph_manager = GraphMemoryManager(
        graph_store=graph_store,
        graph_vector_retriever=GraphVectorRetriever(_FakeFaissDB()),
        graph_extractor=GraphExtractor(),
    )

    metadata = {
        "session_id": "test:private:s1",
        "persona_id": "persona_1",
        "importance": 0.8,
        "create_time": 1.0,
        "last_access_time": 1.0,
        "canonical_summary": "项目会议安排在明天下午三点",
        "topics": ["项目会议"],
        "participants": ["张三", "李四"],
        "key_facts": ["明天下午三点开会"],
    }

    await graph_manager.index_memory(1, metadata["canonical_summary"], metadata)

    stats = await graph_store.get_memory_entry_stats()
    assert stats["graph_nodes"] >= 4
    assert stats["graph_edges"] >= 3
    assert stats["graph_entries"] >= 4


@pytest.mark.asyncio
async def test_graph_store_snapshot_builds_ui_ready_subgraphs(tmp_path: Path):
    db_path = tmp_path / "graph_snapshot.db"
    graph_store = GraphStore(str(db_path))
    await graph_store.initialize()

    manager = GraphMemoryManager(
        graph_store=graph_store,
        graph_vector_retriever=GraphVectorRetriever(_FakeFaissDB()),
        graph_extractor=GraphExtractor(),
    )

    await manager.index_memory(
        11,
        "Roadmap workshop with Alice",
        {
            "session_id": "test:private:s1",
            "persona_id": "persona_1",
            "importance": 0.82,
            "create_time": 10.0,
            "last_access_time": 12.0,
            "canonical_summary": "Roadmap workshop with Alice",
            "topics": ["roadmap"],
            "participants": ["Alice"],
            "key_facts": ["Finalize Q2 roadmap"],
        },
    )
    await manager.index_memory(
        12,
        "Deployment sync with Bob",
        {
            "session_id": "test:private:s1",
            "persona_id": "persona_1",
            "importance": 0.74,
            "create_time": 11.0,
            "last_access_time": 13.0,
            "canonical_summary": "Deployment sync with Bob",
            "topics": ["deployment"],
            "participants": ["Bob"],
            "key_facts": ["Prepare release checklist"],
        },
    )

    snapshot = await graph_store.get_graph_snapshot(session_id="test:private:s1")
    assert snapshot["nodes"]
    assert snapshot["edges"]
    assert snapshot["entries"]
    assert {memory["memory_id"] for memory in snapshot["memories"]} >= {11, 12}

    focused = await graph_store.get_subgraph_for_memories([11])
    assert focused["memories"]
    assert focused["memories"][0]["memory_id"] == 11
    assert all(entry["memory_id"] == 11 for entry in focused["entries"])
    assert any(node["type"] == "person" for node in focused["nodes"])


@pytest.mark.asyncio
async def test_graph_retriever_supports_keyword_and_vector_search(tmp_path: Path):
    db_path = tmp_path / "graph_search.db"
    graph_store = GraphStore(str(db_path))
    await graph_store.initialize()
    vector_db = _FakeFaissDB()

    manager = GraphMemoryManager(
        graph_store=graph_store,
        graph_vector_retriever=GraphVectorRetriever(vector_db),
        graph_extractor=GraphExtractor(),
    )
    metadata = {
        "session_id": "test:private:s1",
        "persona_id": "persona_1",
        "importance": 0.7,
        "create_time": 1.0,
        "last_access_time": 1.0,
        "canonical_summary": "项目讨论记录",
        "topics": ["项目讨论"],
        "participants": ["张三"],
        "key_facts": ["明天下午三点开会"],
    }
    await manager.index_memory(5, metadata["canonical_summary"], metadata)

    text_processor = TextProcessor()
    retriever = GraphRetriever(
        keyword_retriever=GraphKeywordRetriever(graph_store, text_processor),
        vector_retriever=GraphVectorRetriever(vector_db),
        rrf_fusion=RRFFusion(60),
        config={},
    )

    results = await retriever.search("张三", k=3, session_id="test:private:s1")
    assert results
    assert results[0].doc_id == 5
    assert (results[0].keyword_score or 0) > 0

    results = await retriever.search(
        "明天下午三点开会", k=3, session_id="test:private:s1"
    )
    assert results
    assert results[0].doc_id == 5
    assert (results[0].vector_score or 0) > 0


@pytest.mark.asyncio
async def test_memory_engine_dual_route_promotes_graph_hits(tmp_path: Path):
    doc_db_path = tmp_path / "memory.db"
    engine = MemoryEngine(
        db_path=str(doc_db_path),
        faiss_db=_FakeFaissDB(),
        graph_vector_db=_FakeFaissDB(),
        config={
            "fallback_enabled": True,
            "graph_memory_enabled": True,
            "document_route_weight": 0.6,
            "graph_route_weight": 0.4,
        },
    )
    await engine.initialize()

    matching_id = await engine.add_memory(
        content="项目讨论记录",
        session_id="test:private:s1",
        persona_id="persona_1",
        importance=0.8,
        metadata={
            "topics": ["项目讨论"],
            "participants": ["张三"],
            "key_facts": ["明天下午三点开会"],
            "canonical_summary": "项目讨论记录",
        },
    )
    other_id = await engine.add_memory(
        content="普通对话记录",
        session_id="test:private:s1",
        persona_id="persona_1",
        importance=0.5,
        metadata={
            "topics": ["闲聊"],
            "participants": ["王五"],
            "key_facts": ["天气不错"],
            "canonical_summary": "普通对话记录",
        },
    )

    assert matching_id != other_id

    results = await engine.search_memories(
        query="张三",
        k=2,
        session_id="test:private:s1",
        persona_id="persona_1",
    )
    assert results
    assert results[0].doc_id == matching_id
    assert (results[0].score_breakdown or {}).get("graph_keyword_score", 0.0) > 0

    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_user_filter_applies_to_dual_route(tmp_path: Path):
    doc_db_path = tmp_path / "memory_user_filter.db"
    engine = MemoryEngine(
        db_path=str(doc_db_path),
        faiss_db=_FakeFaissDB(),
        graph_vector_db=_FakeFaissDB(),
        config={
            "fallback_enabled": True,
            "graph_memory_enabled": True,
            "document_route_weight": 0.6,
            "graph_route_weight": 0.4,
        },
    )
    await engine.initialize()

    await engine.add_memory(
        content="共同项目讨论",
        session_id="test:private:session_A",
        persona_id="persona_1",
        importance=0.8,
        metadata={
            "user_ids": ["user-A"],
            "primary_user_id": "user-A",
            "topics": ["项目讨论"],
            "participants": ["张三"],
            "key_facts": ["共同项目需要周五前交付"],
            "canonical_summary": "共同项目讨论",
        },
    )
    await engine.add_memory(
        content="共同项目讨论",
        session_id="test:private:session_B",
        persona_id="persona_1",
        importance=0.8,
        metadata={
            "user_ids": ["user-B"],
            "primary_user_id": "user-B",
            "topics": ["项目讨论"],
            "participants": ["李四"],
            "key_facts": ["共同项目需要周五前交付"],
            "canonical_summary": "共同项目讨论",
        },
    )

    results = await engine.search_memories(
        query="共同项目",
        k=5,
        persona_id="persona_1",
        user_id="user-A",
    )

    assert results
    assert all("user-A" in result.metadata.get("user_ids", []) for result in results)
    await engine.close()


@pytest.mark.asyncio
async def test_memory_engine_rebuild_graph_index(tmp_path: Path):
    doc_db_path = tmp_path / "memory_rebuild.db"
    engine = MemoryEngine(
        db_path=str(doc_db_path),
        faiss_db=_FakeFaissDB(),
        graph_vector_db=_FakeFaissDB(),
        config={"fallback_enabled": True, "graph_memory_enabled": True},
    )
    await engine.initialize()

    memory_id = await engine.add_memory(
        content="项目讨论记录",
        session_id="test:private:s1",
        persona_id="persona_1",
        importance=0.8,
        metadata={
            "topics": ["项目讨论"],
            "participants": ["张三"],
            "key_facts": ["明天下午三点开会"],
            "canonical_summary": "项目讨论记录",
        },
    )
    assert memory_id > 0

    assert engine.graph_memory_manager is not None
    await engine.graph_memory_manager.delete_memory(memory_id)

    rebuild_result = await engine.rebuild_graph_index()
    assert rebuild_result["rebuilt"] >= 1

    stats = await engine.get_statistics()
    assert stats.get("graph_entries", 0) >= 1
    await engine.close()

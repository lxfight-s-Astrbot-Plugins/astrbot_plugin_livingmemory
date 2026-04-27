"""
IndexValidator 测试。
"""

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import faiss
import numpy as np
import pytest


def _load_index_validator_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "core/validators/index_validator.py"
    )
    spec = importlib.util.spec_from_file_location("index_validator_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


index_validator_module = _load_index_validator_module()
IndexValidator = index_validator_module.IndexValidator


class _DummyTextProcessor:
    def preprocess_for_bm25(self, text: str) -> str:
        return text


class _DummyBM25Retriever:
    fts_table = "livingmemory_memories_fts"

    def __init__(self):
        self.text_processor = _DummyTextProcessor()


class _DummyEmbeddingProvider:
    def __init__(self, fail_contents: set[str] | None = None):
        self.fail_contents = fail_contents or set()
        self.calls: list[list[str]] = []

    async def get_embeddings_batch(
        self,
        contents: list[str],
        batch_size: int = 32,
        tasks_limit: int = 1,
        max_retries: int = 1,
    ) -> list[list[float]]:
        self.calls.append(list(contents))
        if any(content in self.fail_contents for content in contents):
            raise RuntimeError("模拟 embedding 批次失败")
        return [[float(len(content)), float(index + 1)] for index, content in enumerate(contents)]


class _DummyEmbeddingStorage:
    def __init__(self, index_path: Path):
        self.dimension = 2
        self.path = str(index_path)
        self.index = faiss.IndexIDMap(faiss.IndexFlatL2(self.dimension))

    async def insert_batch(self, vectors: np.ndarray, ids: list[int]) -> None:
        self.index.add_with_ids(vectors, np.asarray(ids, dtype=np.int64))
        faiss.write_index(self.index, self.path)


class _DummyFaissDB:
    def __init__(self, index_path: Path, provider: _DummyEmbeddingProvider):
        self.embedding_provider = provider
        self.embedding_storage = _DummyEmbeddingStorage(index_path)


class _DummyMemoryEngine:
    def __init__(
        self,
        db_path: Path,
        index_path: Path,
        provider: _DummyEmbeddingProvider,
        *,
        batch_size: int = 2,
        failure_ratio: float = 0.02,
    ):
        self.db_path = str(db_path)
        self.faiss_db = _DummyFaissDB(index_path, provider)
        self.bm25_retriever = _DummyBM25Retriever()
        self.config = {
            "index_rebuild_batch_size": batch_size,
            "index_rebuild_embedding_batch_size": batch_size,
            "index_rebuild_tasks_limit": 1,
            "index_rebuild_max_retries": 1,
            "index_rebuild_retry_base_delay": 0,
            "index_rebuild_batch_delay": 0,
            "index_rebuild_request_delay": 0,
            "index_rebuild_max_failure_ratio": failure_ratio,
        }


class _DirectEmbeddingProvider:
    def __init__(self, fail_once: bool = False):
        self.fail_once = fail_once
        self.calls: list[list[str]] = []

    async def get_embeddings(self, contents: list[str]) -> list[list[float]]:
        self.calls.append(list(contents))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("Error code: 429 - TPM limit reached")
        return [[float(len(content)), 1.0] for content in contents]


def _prepare_db(db_path: Path, count: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at REAL,
                updated_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE livingmemory_memories_fts (
                doc_id INTEGER,
                content TEXT
            )
            """
        )

        for index in range(count):
            metadata = {
                "session_id": "test:group:abc",
                "persona_id": "persona_default",
                "importance": 0.5,
                "create_time": time.time(),
                "last_access_time": time.time(),
            }
            cursor = conn.execute(
                "INSERT INTO documents (doc_id, text, metadata) VALUES (?, ?, ?)",
                (
                    f"legacy-{index}",
                    f"doc-{index}",
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            inserted_id = cursor.lastrowid
            if inserted_id is None:
                raise RuntimeError("测试数据写入失败")
            conn.execute(
                "INSERT INTO livingmemory_memories_fts (doc_id, content) VALUES (?, ?)",
                (int(inserted_id), f"old-doc-{index}"),
            )
        conn.commit()


def _count_rows(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        assert row is not None
        return int(row[0])


def _document_doc_ids(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT doc_id FROM documents ORDER BY id").fetchall()
        return [str(row[0]) for row in rows]


def _write_vectors(storage: _DummyEmbeddingStorage, ids: list[int]) -> None:
    vectors = np.asarray([[float(doc_id), 1.0] for doc_id in ids], dtype=np.float32)
    storage.index.add_with_ids(vectors, np.asarray(ids, dtype=np.int64))
    faiss.write_index(storage.index, storage.path)


@pytest.mark.asyncio
async def test_embed_batch_splits_requests_and_waits_between_requests(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(index_validator_module.asyncio, "sleep", fake_sleep)
    provider = _DirectEmbeddingProvider()
    validator = IndexValidator(":memory:", faiss_db=None)

    vectors = await validator._embed_batch_with_retry(
        provider,
        ["a", "bb", "ccc"],
        {
            "embedding_batch_size": 2,
            "max_retries": 1,
            "retry_base_delay": 0,
            "request_delay": 5,
        },
    )

    assert provider.calls == [["a", "bb"], ["ccc"]]
    assert sleeps == [5]
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]


@pytest.mark.asyncio
async def test_rate_limit_retry_uses_minimum_wait(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(index_validator_module.asyncio, "sleep", fake_sleep)
    provider = _DirectEmbeddingProvider(fail_once=True)
    validator = IndexValidator(":memory:", faiss_db=None)

    vectors = await validator._embed_batch_with_retry(
        provider,
        ["limited"],
        {
            "embedding_batch_size": 8,
            "max_retries": 2,
            "retry_base_delay": 1,
            "request_delay": 0,
        },
    )

    assert sleeps == [30.0]
    assert provider.calls == [["limited"], ["limited"]]
    assert vectors == [[7.0, 1.0]]


@pytest.mark.asyncio
async def test_rebuild_indexes_preserves_documents_and_batches_embeddings(
    tmp_path: Path,
):
    db_path = tmp_path / "memory.db"
    index_path = tmp_path / "memory.index"
    _prepare_db(db_path, count=5)

    provider = _DummyEmbeddingProvider()
    memory_engine = _DummyMemoryEngine(db_path, index_path, provider, batch_size=2)
    validator = IndexValidator(str(db_path), faiss_db=memory_engine.faiss_db)

    result = await validator.rebuild_indexes(memory_engine=memory_engine)

    assert result["success"] is True
    assert result["processed"] == 5
    assert result["errors"] == 0
    assert result["vector_mode"] == "full"
    assert result["switched"] is True
    assert provider.calls == [["doc-0", "doc-1"], ["doc-2", "doc-3"], ["doc-4"]]
    assert _count_rows(db_path, "documents") == 5
    assert _count_rows(db_path, "livingmemory_memories_fts") == 5
    assert _document_doc_ids(db_path) == [f"legacy-{index}" for index in range(5)]
    assert memory_engine.faiss_db.embedding_storage.index.ntotal == 5


@pytest.mark.asyncio
async def test_rebuild_indexes_does_not_switch_when_failure_ratio_is_too_high(
    tmp_path: Path,
):
    db_path = tmp_path / "memory.db"
    index_path = tmp_path / "memory.index"
    _prepare_db(db_path, count=5)

    provider = _DummyEmbeddingProvider(fail_contents={"doc-2"})
    memory_engine = _DummyMemoryEngine(
        db_path,
        index_path,
        provider,
        batch_size=2,
        failure_ratio=0.2,
    )
    validator = IndexValidator(str(db_path), faiss_db=memory_engine.faiss_db)

    result = await validator.rebuild_indexes(memory_engine=memory_engine)

    assert result["success"] is False
    assert result["switched"] is False
    assert result["errors"] == 2
    assert result["failure_ratio"] == pytest.approx(0.4)
    assert _count_rows(db_path, "documents") == 5
    assert memory_engine.faiss_db.embedding_storage.index.ntotal == 0


@pytest.mark.asyncio
async def test_rebuild_indexes_switches_partial_index_when_failure_ratio_is_acceptable(
    tmp_path: Path,
):
    db_path = tmp_path / "memory.db"
    index_path = tmp_path / "memory.index"
    _prepare_db(db_path, count=10)

    provider = _DummyEmbeddingProvider(fail_contents={"doc-9"})
    memory_engine = _DummyMemoryEngine(
        db_path,
        index_path,
        provider,
        batch_size=1,
        failure_ratio=0.2,
    )
    validator = IndexValidator(str(db_path), faiss_db=memory_engine.faiss_db)

    result = await validator.rebuild_indexes(memory_engine=memory_engine)

    assert result["success"] is True
    assert result["partial"] is True
    assert result["switched"] is True
    assert result["errors"] == 1
    assert memory_engine.faiss_db.embedding_storage.index.ntotal == 9
    assert _count_rows(db_path, "documents") == 10


@pytest.mark.asyncio
async def test_rebuild_indexes_repairs_only_missing_vectors_when_index_is_readable(
    tmp_path: Path,
):
    db_path = tmp_path / "memory.db"
    index_path = tmp_path / "memory.index"
    _prepare_db(db_path, count=5)

    provider = _DummyEmbeddingProvider()
    memory_engine = _DummyMemoryEngine(db_path, index_path, provider, batch_size=2)
    _write_vectors(memory_engine.faiss_db.embedding_storage, [1, 2, 3, 4])
    validator = IndexValidator(str(db_path), faiss_db=memory_engine.faiss_db)

    result = await validator.rebuild_indexes(memory_engine=memory_engine)

    assert result["success"] is True
    assert result["vector_mode"] == "repair"
    assert provider.calls == [["doc-4"]]
    assert memory_engine.faiss_db.embedding_storage.index.ntotal == 5
    assert _count_rows(db_path, "documents") == 5

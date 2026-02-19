"""
Tests for IndexValidator.
"""

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest


def _load_index_validator_class():
    module_path = (
        Path(__file__).resolve().parents[1] / "core/validators/index_validator.py"
    )
    spec = importlib.util.spec_from_file_location("index_validator_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.IndexValidator


IndexValidator = _load_index_validator_class()


class _DummyEmbeddingStorage:
    def __init__(self, db_path: Path, lock_stats: dict[str, int]):
        self._db_path = db_path
        self._lock_stats = lock_stats

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            return
        try:
            with sqlite3.connect(self._db_path, timeout=0) as conn:
                conn.execute("DELETE FROM documents WHERE id = ?", (int(ids[0]),))
                conn.commit()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                self._lock_stats["locked_errors"] += 1
            raise


class _DummyFaissDB:
    def __init__(self, db_path: Path, lock_stats: dict[str, int]):
        self._db_path = db_path
        self._lock_stats = lock_stats
        self.embedding_storage = _DummyEmbeddingStorage(db_path, lock_stats)

    async def delete(self, doc_uuid: str) -> None:
        try:
            with sqlite3.connect(self._db_path, timeout=0) as conn:
                conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_uuid,))
                conn.commit()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                self._lock_stats["locked_errors"] += 1
            raise


class _DummyMemoryEngine:
    def __init__(self, db_path: Path, lock_stats: dict[str, int]):
        self._db_path = db_path
        self.faiss_db = _DummyFaissDB(db_path, lock_stats)

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> int:
        metadata = metadata or {}
        metadata.setdefault("session_id", session_id)
        metadata.setdefault("persona_id", persona_id)
        metadata.setdefault("importance", importance)
        metadata.setdefault("create_time", time.time())
        metadata.setdefault("last_access_time", time.time())

        with sqlite3.connect(self._db_path, timeout=5) as conn:
            cursor = conn.execute(
                "INSERT INTO documents (doc_id, text, metadata) VALUES (?, ?, ?)",
                (
                    f"new-{time.time_ns()}",
                    content,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            new_id_raw = cursor.lastrowid
            if new_id_raw is None:
                raise RuntimeError("failed to insert document: lastrowid is None")
            new_id = int(new_id_raw)
            conn.execute(
                "INSERT INTO memories_fts (doc_id, content) VALUES (?, ?)",
                (new_id, content),
            )
            conn.commit()
        return new_id


def _prepare_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE memories_fts (
                doc_id INTEGER,
                content TEXT
            )
            """
        )

        for i in range(3):
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
                    f"legacy-{i}",
                    f"doc-{i}",
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            inserted_id = cursor.lastrowid
            if inserted_id is None:
                raise RuntimeError("failed to seed document: lastrowid is None")
            conn.execute(
                "INSERT INTO memories_fts (doc_id, content) VALUES (?, ?)",
                (int(inserted_id), f"doc-{i}"),
            )
        conn.commit()


@pytest.mark.asyncio
async def test_rebuild_indexes_avoids_sqlite_lock_during_faiss_delete(tmp_path: Path):
    """
    Regression test for sqlite lock during index rebuild.

    The old implementation kept an aiosqlite write transaction open while calling
    faiss_db.delete(), which also writes to `documents` via another connection.
    """

    db_path = tmp_path / "rebuild_lock_test.db"
    _prepare_db(db_path)

    lock_stats = {"locked_errors": 0}
    memory_engine = _DummyMemoryEngine(db_path, lock_stats)
    validator = IndexValidator(str(db_path), faiss_db=memory_engine.faiss_db)

    result = await validator.rebuild_indexes(memory_engine=memory_engine)

    assert result["success"] is True
    assert result["processed"] == 3
    assert lock_stats["locked_errors"] == 0

    with sqlite3.connect(db_path) as conn:
        doc_count_row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        fts_count_row = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()
    assert doc_count_row is not None
    assert fts_count_row is not None
    doc_count = int(doc_count_row[0])
    fts_count = int(fts_count_row[0])

    assert doc_count == 3
    assert fts_count == 3

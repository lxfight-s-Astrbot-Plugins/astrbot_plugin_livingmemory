"""Regression tests for /lmem rebuild-graph embedding batching (#429)."""

from pathlib import Path

import aiosqlite
import pytest

from astrbot_plugin_livingmemory.core.managers.memory_engine_batch import (
    MemoryEngineBatchMixin,
)


class _GraphManager:
    """Capture the vector batch size passed by rebuild_graph_index."""

    def __init__(self) -> None:
        self.vector_batch_size: int | None = None

    async def rebuild_memory_batches(self, batches, *, vector_batch_size=100):
        self.vector_batch_size = vector_batch_size
        async for _ in batches:
            pass
        return {"rebuilt": 0, "skipped": 0}


class _Engine(MemoryEngineBatchMixin):
    """Minimal host exposing only what rebuild_graph_index touches."""

    def __init__(self, db_connection, graph_memory_manager, config):
        self.db_connection = db_connection
        self.graph_memory_manager = graph_memory_manager
        self.config = config

    def _invalidate_search_cache(self) -> None:
        pass


async def _documents_db(tmp_path: Path):
    db = await aiosqlite.connect(str(tmp_path / "memory.db"))
    db.row_factory = aiosqlite.Row
    await db.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
    )
    await db.execute(
        "INSERT INTO documents (id, text, metadata) VALUES (?, ?, ?)",
        (1, "hello", '{"status": "active"}'),
    )
    await db.commit()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (20, 20),
        (1, 1),
        (0, 1),
        (999, 256),
        ("not-an-int", 8),
        (None, 8),
    ],
)
async def test_rebuild_graph_honors_configured_embedding_batch_size(
    tmp_path: Path, configured, expected
):
    db = await _documents_db(tmp_path)
    graph_manager = _GraphManager()
    engine = _Engine(
        db, graph_manager, {"index_rebuild_embedding_batch_size": configured}
    )

    await engine.rebuild_graph_index()

    assert graph_manager.vector_batch_size == expected
    await db.close()


@pytest.mark.asyncio
async def test_rebuild_graph_defaults_to_settings_default_without_config(
    tmp_path: Path,
):
    db = await _documents_db(tmp_path)
    graph_manager = _GraphManager()
    engine = _Engine(db, graph_manager, {})

    await engine.rebuild_graph_index()

    assert graph_manager.vector_batch_size == 8
    await db.close()

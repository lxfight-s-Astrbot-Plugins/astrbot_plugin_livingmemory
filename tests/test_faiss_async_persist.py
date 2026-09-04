"""
FAISS 索引异步落盘（core/faiss_async_persist.py）测试。
"""

import asyncio
import os
import time
from pathlib import Path

import faiss
import numpy as np
import pytest

from astrbot_plugin_livingmemory.core import faiss_async_persist
from astrbot_plugin_livingmemory.core.faiss_async_persist import (
    get_async_persister,
    install_async_persist,
)


class _DummyEmbeddingStorage:
    """最小化模拟 AstrBot EmbeddingStorage：真实 faiss 索引 + 真实文件路径。"""

    def __init__(self, index_path: Path, dimension: int = 8):
        self.dimension = dimension
        self.path = str(index_path)
        self.index = faiss.IndexIDMap(faiss.IndexFlatL2(dimension))

    @staticmethod
    def _write_index(index, path) -> None:
        faiss.write_index(index, path)

    async def save_index(self) -> None:
        # 与 AstrBot 一致的原始行为：同步整文件写
        if self.index is None or not self.path:
            return
        self._write_index(self.index, self.path)

    async def insert_batch(self, vectors: np.ndarray, ids: list[int]) -> None:
        # 与 AstrBot 一致：先改索引再触发 save_index
        self.index.add_with_ids(vectors, np.asarray(ids, dtype=np.int64))
        await self.save_index()


def _add_vectors(storage: _DummyEmbeddingStorage, start_id: int, count: int) -> None:
    vectors = np.random.rand(count, storage.dimension).astype(np.float32)
    ids = np.arange(start_id, start_id + count, dtype=np.int64)
    storage.index.add_with_ids(vectors, ids)


def _make_storage(tmp_path: Path, name: str = "test.index") -> _DummyEmbeddingStorage:
    return _DummyEmbeddingStorage(tmp_path / name)


@pytest.mark.asyncio
async def test_save_index_debounces_into_single_write(tmp_path, monkeypatch):
    """防抖窗口内的多次 save_index 只落盘一次。"""
    storage = _make_storage(tmp_path)
    _add_vectors(storage, 0, 10)
    persister = install_async_persist(storage, debounce_seconds=0.05)

    write_count = 0
    real_write = faiss_async_persist._write_index_atomic

    def counting_write(index, path):
        nonlocal write_count
        write_count += 1
        real_write(index, path)

    monkeypatch.setattr(faiss_async_persist, "_write_index_atomic", counting_write)

    for _ in range(5):
        await storage.save_index()
    # 防抖窗口未结束，不应有任何写盘
    assert write_count == 0

    await asyncio.sleep(0.3)
    assert write_count == 1

    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == 10
    await persister.aclose()


@pytest.mark.asyncio
async def test_flush_now_writes_pending_immediately(tmp_path):
    """长防抖窗口下 flush_now 立即落盘且文件可读回。"""
    storage = _make_storage(tmp_path)
    _add_vectors(storage, 0, 20)
    persister = install_async_persist(storage, debounce_seconds=600)

    await storage.save_index()
    assert not Path(storage.path).exists()

    await persister.flush_now()
    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == 20
    await persister.aclose()


@pytest.mark.asyncio
async def test_mutation_during_inflight_flush(tmp_path):
    """落盘进行中的并发写入不崩溃，最终文件内容与内存索引一致。"""
    storage = _make_storage(tmp_path)
    _add_vectors(storage, 0, 500)
    persister = install_async_persist(storage, debounce_seconds=0)

    await storage.save_index()
    # 让第一次落盘先开始（写盘在 executor 线程）
    await asyncio.sleep(0.05)
    # 走被变更锁包裹的 insert_batch：排在进行中的写盘之后，不与其并发改索引
    vectors = np.random.rand(500, storage.dimension).astype(np.float32)
    await storage.insert_batch(vectors, list(range(500, 1000)))

    await persister.flush_now()
    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == storage.index.ntotal == 1000
    await persister.aclose()


@pytest.mark.asyncio
async def test_aclose_flushes_pending_and_is_idempotent(tmp_path):
    """aclose 落盘未写变更、关闭线程池，且可重复调用。"""
    storage = _make_storage(tmp_path)
    _add_vectors(storage, 0, 10)
    persister = install_async_persist(storage, debounce_seconds=600)

    await storage.save_index()
    assert not Path(storage.path).exists()

    await persister.aclose()
    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == 10

    # 关闭后 save_index 静默忽略，aclose 幂等
    _add_vectors(storage, 10, 5)
    await storage.save_index()
    await persister.aclose()
    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == 10


@pytest.mark.asyncio
async def test_zero_debounce_persists_without_delay(tmp_path):
    """debounce=0 时每次变更立即安排异步落盘。"""
    storage = _make_storage(tmp_path)
    _add_vectors(storage, 0, 10)
    persister = install_async_persist(storage, debounce_seconds=0)

    await storage.save_index()
    await persister.flush_now()
    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == 10
    await persister.aclose()


@pytest.mark.asyncio
async def test_flush_now_guards_rebuild_file_swap(tmp_path, monkeypatch):
    """索引重建换文件前必须先 flush_now 排空飞行中的落盘。

    锁定 core/validators/index_validator_rebuild.py 中 flush_now 调用的必要性：
    去掉它时，飞行中的旧索引落盘会在 os.replace 之后落地，覆盖重建结果。
    """
    storage = _make_storage(tmp_path)
    _add_vectors(storage, 0, 2000)
    persister = install_async_persist(storage, debounce_seconds=0)

    real_write = faiss_async_persist._write_index_atomic

    def slow_write(index, path):
        # 放慢写盘，制造「换文件时落盘仍在飞行中」的真实窗口
        time.sleep(0.3)
        real_write(index, path)

    monkeypatch.setattr(faiss_async_persist, "_write_index_atomic", slow_write)

    # 重建产物：只含 50 条向量的新索引
    rebuilt = faiss.IndexIDMap(faiss.IndexFlatL2(storage.dimension))
    rebuilt.add_with_ids(
        np.random.rand(50, storage.dimension).astype(np.float32),
        np.arange(10_000, 10_050, dtype=np.int64),
    )
    temp_path = f"{storage.path}.rebuild.tmp"
    faiss.write_index(rebuilt, temp_path)

    await storage.save_index()
    await asyncio.sleep(0.05)  # 旧索引落盘已进入飞行
    await persister.flush_now()
    os.replace(temp_path, storage.path)
    storage.index = rebuilt
    await asyncio.sleep(0.5)  # 给漏网的落盘留出落地时间

    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == 50, "重建结果被飞行中的旧索引落盘覆盖"
    await persister.aclose()


def test_install_is_per_instance_and_idempotent(tmp_path):
    """实例级替换：不影响其他实例；重复安装复用同一 persister。"""
    storage_a = _make_storage(tmp_path, "a.index")
    storage_b = _make_storage(tmp_path, "b.index")

    persister_a = install_async_persist(storage_a, debounce_seconds=1.0)
    assert persister_a is not None
    assert get_async_persister(storage_a) is persister_a
    assert get_async_persister(storage_b) is None
    # storage_b 的 save_index 仍是类方法，未被替换
    assert "save_index" not in storage_b.__dict__

    assert install_async_persist(storage_a, debounce_seconds=9.0) is persister_a


def test_install_skips_storage_without_path(tmp_path):
    """纯内存模式（无索引路径）不安装，与原 save_index 空操作语义一致。"""

    class _MemoryOnlyStorage:
        path = None
        index = None

    assert install_async_persist(_MemoryOnlyStorage(), 1.0) is None


@pytest.mark.asyncio
async def test_write_failure_keeps_dirty_for_retry(tmp_path, monkeypatch):
    """写盘失败保持 dirty，下次 flush_now 可重试成功。"""
    storage = _make_storage(tmp_path)
    _add_vectors(storage, 0, 10)
    persister = install_async_persist(storage, debounce_seconds=0)

    real_write = faiss_async_persist._write_index_atomic
    state = {"fail": True}

    def flaky_write(index, path):
        if state["fail"]:
            raise OSError("disk full")
        real_write(index, path)

    monkeypatch.setattr(faiss_async_persist, "_write_index_atomic", flaky_write)

    await storage.save_index()
    with pytest.raises(OSError):
        await persister.flush_now()
    assert persister._dirty

    state["fail"] = False
    await persister.flush_now()
    assert not persister._dirty
    loaded = faiss.read_index(storage.path)
    assert loaded.ntotal == 10
    await persister.aclose()

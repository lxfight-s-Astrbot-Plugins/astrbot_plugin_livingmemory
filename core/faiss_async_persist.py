"""FAISS 索引异步落盘（变更锁 + 线程写盘 + 防抖合并）。

背景：AstrBot 的 ``EmbeddingStorage.save_index()`` 在事件循环上同步执行
``faiss.write_index``，对整个索引做整文件重写。索引随记忆增长到数百 MB 后，
每次新增/删除记忆都会阻塞事件循环数秒，慢盘服务器上甚至达到分钟级。

本模块在**实例级**替换 ``save_index``，并给 ``insert``/``insert_batch``/``delete``
套上一把变更锁：

1. 读写锁（asyncio readers-writer lock）把「修改索引」与「查询/写盘」分离。
   增删是写者，独占索引；查询与写盘都是读者，可以互相并发（两者都只读索引）。
   写盘期间查询不再排队，只有新的增删等待写盘完成；查询与写盘分别跑在不同
   线程池（写盘用单线程 executor 保证顺序，查询用默认线程池），互不阻塞；
2. 写盘在单线程 executor 中执行 ``faiss.write_index``：流式写盘，不额外占用
   与索引等量的内存；先写同目录临时文件再 ``os.replace`` 原子覆盖，同卷
   rename 原子，进程中途被杀不会留下写了一半的索引文件。Windows 非 ASCII
   路径改经 ``write_index_safe`` 的纯 ASCII 临时文件中转，不再只依赖
   ``faiss.write_index`` 的全局 monkey-patch（见 core/plugin_initializer_faiss.py，#290）；
3. 防抖合并：``save_index`` 只标记脏并排一个延迟落盘任务，任务醒来时若已有
   更新的落盘请求（序号守卫）就直接跳过，于是窗口内的多次变更只写盘一次。

读路径（search）只持有读锁：写盘只读索引，与查询并发安全（本插件只使用
IndexIDMap(IndexFlatL2)，其查询不在索引上留下可变状态）；读锁的作用仅是
阻止增删在查询/写盘进行中改动索引（含查询被取消后工作线程仍在跑的场景）。

代价：防抖窗口内进程崩溃会丢失最近几秒未落盘的向量变更。
启动时的索引校验（core/validators/）会对账 documents 表与索引并自动修复，
该丢失在既有兜底能力范围内。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import os
import tempfile
from contextlib import asynccontextmanager
from typing import Any

from astrbot.api import logger

from .utils.faiss_path_bridge import needs_bridge, write_index_safe

_PERSISTER_ATTR = "_async_index_persister"
# 会被变更锁包裹的索引写方法（save_index 单独整体替换）
_MUTATION_METHODS = ("insert", "insert_batch", "delete")
# 默认防抖窗口：窗口内的多次索引变更合并为一次落盘
DEFAULT_DEBOUNCE_SECONDS = 3.0


def _write_index_atomic(index: Any, path: str) -> None:
    """把索引原子写入 path（在 executor 线程中执行）。

    纯 ASCII 路径：同目录临时文件 + ``os.replace``，保证 rename 同卷原子，
    进程中途被杀也不会留下写了一半的索引文件。
    非 ASCII 路径：交给 ``write_index_safe`` 经纯 ASCII 临时文件桥接，
    FAISS 临时文件不会创建在非 ASCII 目录，也不再依赖全局 monkey-patch（#290）。
    """
    if needs_bridge(path):
        write_index_safe(index, path)
        return

    import faiss

    dirname = os.path.dirname(path) or "."
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".lmem_faiss_", suffix=".tmp", dir=dirname)
    os.close(fd)
    try:
        faiss.write_index(index, tmp_path)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class _ReadersWriterLock:
    """asyncio 读写锁：查询/写盘可并发，增删独占。

    本模块只需「读者之间并发、写者与所有读者互斥」的语义，不追求写者优先，
    因此用 Condition 实现最小版本：读者计数 + 单个写者标记。
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False

    @asynccontextmanager
    async def read(self):
        async with self._condition:
            while self._writer:
                await self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @asynccontextmanager
    async def write(self):
        async with self._condition:
            while self._writer or self._readers:
                await self._condition.wait()
            self._writer = True
        try:
            yield
        finally:
            async with self._condition:
                self._writer = False
                self._condition.notify_all()


class AsyncIndexPersister:
    """把某个 EmbeddingStorage 实例的落盘替换为防抖异步写。"""

    def __init__(
        self, storage: Any, debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS
    ) -> None:
        self._storage = storage
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        # 读者 = search/落盘；写者 = insert/insert_batch/delete
        self._index_lock = _ReadersWriterLock()
        # 落盘本身串行（避免两次 flush 同时写同一文件）
        self._flush_lock = asyncio.Lock()
        # 单线程 executor：写盘严格按提交顺序串行
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="lmem-faiss-flush"
        )
        self._debounce_tasks: set[asyncio.Task] = set()
        self._pending_write: asyncio.Future | None = None
        self._dirty = False
        self._seq = 0
        self._closed = False

    def wrap_mutation_methods(self) -> None:
        """给 insert/insert_batch/delete 套上变更锁（实例级）。"""
        for name in _MUTATION_METHODS:
            original = getattr(self._storage, name, None)
            if not callable(original):
                continue

            @functools.wraps(original)
            async def wrapper(*args, _original=original, **kwargs):
                async with self._index_lock.write():
                    return await _original(*args, **kwargs)

            setattr(self._storage, name, wrapper)

    async def save_index(self) -> None:
        """替换后的 save_index：标记脏并排一个延迟落盘任务。

        这里不取消上一个延迟任务：任务醒来后靠序号守卫判断自己是否已过期，
        合并效果与取消相同；而取消一个已进入写盘的任务，会在写盘线程仍在
        运行时释放变更锁，使后续增删与写盘并发。
        """
        if self._closed:
            return
        if self._storage.index is None or not self._storage.path:
            return
        self._dirty = True
        self._seq += 1
        task = asyncio.create_task(self._debounced_flush(self._seq))
        self._debounce_tasks.add(task)
        task.add_done_callback(self._debounce_tasks.discard)

    async def _debounced_flush(self, seq: int) -> None:
        try:
            if self._debounce_seconds > 0:
                await asyncio.sleep(self._debounce_seconds)
            await self._do_flush(seq)
        except asyncio.CancelledError:
            raise
        except Exception:
            # dirty 保持为 True，下次变更或 flush_now 会重试
            logger.error("[FAISS] 索引异步落盘失败，将在下次变更时重试", exc_info=True)

    async def _do_flush(self, seq: int | None = None, *, force: bool = False) -> None:
        """把当前索引状态写入磁盘。

        Args:
            seq: 调用方看到的落盘序号；非 force 模式下序号过期说明已有更新的
                落盘请求排队，直接跳过以省一次整文件 I/O。
            force: 忽略序号守卫，确保当前状态写盘（flush_now / aclose / 重建换文件前）。
        """
        if not force and (seq != self._seq or not self._dirty):
            return
        loop = asyncio.get_running_loop()
        async with self._flush_lock:
            if not self._dirty:
                # 等锁期间已被另一次落盘写掉
                return
            # 写盘是只读操作：只阻止增删，不阻止查询
            async with self._index_lock.read():
                storage = self._storage
                index = storage.index
                path = storage.path
                if index is None or not path:
                    self._dirty = False
                    return
                fut = loop.run_in_executor(
                    self._executor, _write_index_atomic, index, path
                )
                self._pending_write = fut
                try:
                    await asyncio.shield(fut)
                except asyncio.CancelledError:
                    await fut
                    raise
                finally:
                    if self._pending_write is fut:
                        self._pending_write = None
                # 落盘期间增删被读锁挡住，写入内容即当前索引状态，脏标记可整体清掉
                self._dirty = False

    async def flush_now(self) -> None:
        """立即落盘未写变更，并等待进行中的写盘完成。

        索引重建换文件（core/validators/index_validator_rebuild.py）之前必须调用：
        飞行中的落盘写的是换文件之前的索引对象，若在 os.replace 之后才落地，
        会把刚重建好的索引文件覆盖掉。
        """
        if self._closed:
            return
        # 序号自增使所有已排队的延迟落盘任务失效，无需逐个取消
        self._seq += 1
        await self._do_flush(force=True)
        pending = self._pending_write
        if pending is not None:
            await pending

    async def search(self, vector, k):
        """Search off the event loop; only index mutations exclude it.

        Args:
            vector: Validated one-dimensional query vector.
            k: Number of nearest neighbors.

        Returns:
            FAISS distances and integer IDs.
        """
        # 读者锁：只与增删互斥；与落盘可并发（两者都只读索引）
        async with self._index_lock.read():
            future = asyncio.get_running_loop().run_in_executor(
                None, self._storage.index.search, vector.reshape(1, -1), k
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # Cancellation must not let a writer mutate an index still in use.
                await future
                raise

    async def aclose(self) -> None:
        """落盘剩余变更并关闭写盘线程（插件卸载前调用，幂等）。"""
        if self._closed:
            return
        self._closed = True
        self._seq += 1
        # 取消仍在等待防抖窗口的落盘任务，并等它们解开（可能持有变更锁）
        pending_tasks = list(self._debounce_tasks)
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        try:
            if self._dirty:
                await self._do_flush(force=True)
            pending = self._pending_write
            if pending is not None:
                await pending
        except Exception:
            logger.error("[FAISS] 关闭前落盘索引失败", exc_info=True)
        # 关线程池交给默认线程池执行：极端情况下仍有飞行中的写盘时，
        # 不在事件循环上同步等待整文件写完
        await asyncio.get_running_loop().run_in_executor(
            None, self._executor.shutdown, True
        )


def get_async_persister(storage: Any) -> AsyncIndexPersister | None:
    """返回已安装在 storage 上的 persister，未安装则返回 None。"""
    if storage is None:
        return None
    return getattr(storage, _PERSISTER_ATTR, None)


def install_async_persist(
    storage: Any, debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS
) -> AsyncIndexPersister | None:
    """在实例级替换 storage 的落盘行为为异步防抖写。

    只改单个实例的实例属性，不影响 EmbeddingStorage 类和其他实例。
    已安装过则复用原实例；storage 无索引路径（纯内存模式）时返回 None。
    """
    if storage is None or not getattr(storage, "path", None):
        return None
    existing = get_async_persister(storage)
    if existing is not None:
        return existing
    persister = AsyncIndexPersister(storage, debounce_seconds)
    # 先给变更方法套锁，再替换 save_index，保证写盘期间索引静止
    persister.wrap_mutation_methods()
    storage.save_index = persister.save_index
    setattr(storage, _PERSISTER_ATTR, persister)
    return persister

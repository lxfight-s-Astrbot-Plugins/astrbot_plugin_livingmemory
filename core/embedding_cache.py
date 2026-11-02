"""共享 embedding 结果；缓存与长期记忆、FAISS 索引相互独立。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Awaitable, Callable


class EmbeddingCache:
    """按真实输入去重，并将并发 miss 合并为一个可独立完成的任务。"""

    def __init__(
        self,
        path: str,
        namespace: str,
        dimension: int,
        fetch: Callable[[list[str]], Awaitable[tuple[list[list[float]], int]]],
        *,
        ttl_days: int = 90,
        max_entries: int = 10000,
        concurrency: int = 2,
        max_pending: int = 256,
    ):
        self.namespace = namespace
        self.dimension = dimension
        self.fetch = fetch
        self.ttl_seconds = ttl_days * 86400
        self.max_entries = max_entries
        self.max_pending = max_pending
        self._lock = asyncio.Lock()
        self._db_lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(concurrency)
        self._pending: dict[str, asyncio.Future] = {}
        self._recent: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self.path = path
        self._db: sqlite3.Connection | None = None
        self.stats = dict(
            requests=0, inputs=0, cache_hits=0, coalesced=0,
            upstream_requests=0, upstream_inputs=0, upstream_tokens=0, failures=0,
        )

    async def start(self):
        await asyncio.to_thread(self._open)

    def _open(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            "cache_key TEXT PRIMARY KEY, namespace TEXT NOT NULL, dimension INTEGER NOT NULL,"
            "vector BLOB NOT NULL, created_at REAL NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS usage_totals ("
            "namespace TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0,"
            "inputs INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0)"
        )
        self._db.commit()

    def key(self, text: str) -> str:
        # 不去掉空格、时间或标点：缓存键与真正发给模型的输入完全一致。
        return hashlib.sha256(
            json.dumps([self.namespace, self.dimension, text], ensure_ascii=False).encode()
        ).hexdigest()

    def validate(self, vector) -> list[float]:
        if not isinstance(vector, list) or len(vector) != self.dimension:
            raise ValueError("Invalid embedding dimension")
        result = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in result):
            raise ValueError("Non-finite embedding")
        return result

    def _read(self, keys: list[str]) -> dict[str, list[float]]:
        result = {}
        assert self._db is not None
        with self._db_lock:
            for key in keys:
                row = self._db.execute(
                    "SELECT dimension,vector,created_at FROM embeddings "
                    "WHERE cache_key=? AND namespace=?", (key, self.namespace),
                ).fetchone()
                if row is None:
                    continue
                try:
                    if row[0] != self.dimension or time.time() - row[2] >= self.ttl_seconds:
                        continue
                    result[key] = self.validate(
                        list(struct.unpack(f"<{self.dimension}d", row[1]))
                    )
                except (ValueError, TypeError, struct.error):
                    # 损坏缓存只重算该条，不能让缓存损坏变成整个记忆库失效。
                    continue
        return result

    def _write(self, items: list[tuple[str, list[float]]], tokens: int):
        assert self._db is not None
        timestamp = time.time()
        with self._db_lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?)",
                [
                    (key, self.namespace, self.dimension,
                     struct.pack(f"<{self.dimension}d", *vector), timestamp)
                    for key, vector in items
                ],
            )
            self._db.execute(
                "INSERT INTO usage_totals VALUES (?,1,?,?) ON CONFLICT(namespace) "
                "DO UPDATE SET requests=requests+1,inputs=inputs+excluded.inputs,"
                "tokens=tokens+excluded.tokens",
                (self.namespace, len(items), tokens),
            )
            self._db.execute(
                "DELETE FROM embeddings WHERE created_at < ?", (timestamp - self.ttl_seconds,)
            )
            self._db.execute(
                "DELETE FROM embeddings WHERE cache_key IN (SELECT cache_key FROM embeddings "
                "ORDER BY created_at DESC,cache_key DESC LIMIT -1 OFFSET ?)",
                (self.max_entries,),
            )

    async def get(self, texts: list[str]) -> tuple[list[list[float]], int, dict]:
        if self._closed or self._db is None:
            raise RuntimeError("Embedding cache unavailable")
        if not isinstance(texts, list) or any(not isinstance(t, str) or not t for t in texts):
            raise ValueError("Embedding input must contain nonempty strings")
        if len(texts) > 128 or any(len(t) > 32768 for t in texts):
            raise ValueError("Embedding input exceeds gateway limits")
        if not texts:
            return [], 0, {"hits": 0, "misses": 0, "coalesced": 0}
        keys = [self.key(t) for t in texts]
        unique = dict(zip(keys, texts))
        local = dict(hits=0, misses=0, coalesced=0)
        async with self._lock:
            if self._closed:
                raise RuntimeError("Embedding cache unavailable")
            cached = {}
            for key in unique:
                value = self._recent.get(key)
                if value is not None and time.time() - value[0] < self.ttl_seconds:
                    cached[key] = value[1]
                    self._recent.move_to_end(key)
            cached.update(
                await asyncio.to_thread(self._read, [k for k in unique if k not in cached])
            )
            new = {
                k: t for k, t in unique.items()
                if k not in cached and k not in self._pending
            }
            if len(self._pending) + len(new) > self.max_pending:
                raise RuntimeError("Embedding cache busy")
            waiting = {}
            for key in unique:
                if key in cached:
                    local["hits"] += 1
                    continue
                if key in self._pending:
                    local["coalesced"] += 1
                else:
                    self._pending[key] = asyncio.get_running_loop().create_future()
                    self._pending[key].add_done_callback(self._consume_exception)
                    local["misses"] += 1
                waiting[key] = self._pending[key]
            if new:
                task = asyncio.create_task(self._populate(list(new.items())))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            self.stats["requests"] += 1
            self.stats["inputs"] += len(texts)
            self.stats["cache_hits"] += local["hits"]
            self.stats["coalesced"] += local["coalesced"]
        actual_tokens = 0
        for key, future in waiting.items():
            vector, tokens = await asyncio.shield(future)
            cached[key] = vector
            # 只由发起 miss 的请求计实际消耗；等待同一结果者不会重复计费。
            if key in new:
                actual_tokens += tokens
        return [list(cached[key]) for key in keys], actual_tokens, local

    @staticmethod
    def _consume_exception(future):
        if not future.cancelled():
            future.exception()

    async def _populate(self, items: list[tuple[str, str]]):
        try:
            async with self._semaphore:
                for start in range(0, len(items), 10):
                    chunk = items[start:start + 10]
                    self.stats["upstream_requests"] += 1
                    self.stats["upstream_inputs"] += len(chunk)
                    vectors, tokens = await self.fetch([text for _, text in chunk])
                    self.stats["upstream_tokens"] += tokens
                    if len(vectors) != len(chunk):
                        raise ValueError("Incomplete embedding batch")
                    validated = [
                        (key, self.validate(vector))
                        for (key, _), vector in zip(chunk, vectors)
                    ]
                    # 已付费结果必须保留；缓存写失败时仍返回有效向量，避免立即重试。
                    try:
                        await asyncio.to_thread(self._write, validated, tokens)
                    except sqlite3.Error:
                        self.stats["failures"] += 1
                    async with self._lock:
                        for index, (key, vector) in enumerate(validated):
                            self._recent[key] = (time.time(), vector)
                            self._recent.move_to_end(key)
                            while len(self._recent) > min(self.max_entries, 256):
                                self._recent.popitem(last=False)
                            future = self._pending.pop(key)
                            future.set_result((vector, tokens if index == 0 else 0))
        except BaseException as exc:
            self.stats["failures"] += 1
            async with self._lock:
                for key, _ in items:
                    future = self._pending.pop(key, None)
                    if future is not None and not future.done():
                        future.set_exception(
                            RuntimeError("Embedding cache stopping")
                            if isinstance(exc, asyncio.CancelledError) else exc
                        )
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def close(self):
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            if self._db is not None:
                await asyncio.to_thread(self._close_db)

    def _close_db(self):
        with self._db_lock:
            self._db.close()
            self._db = None

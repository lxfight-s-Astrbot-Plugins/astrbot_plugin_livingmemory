"""Benchmark real SQLite/FAISS recall with local embeddings and temporary data."""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


async def benchmark(args):
    """Measure scoped retrieval quality, latency and event-loop scheduling.

    Args:
        args: Parsed benchmark sizes, dimensions and concurrency.

    Returns:
        None. Each data size emits a JSON record to stdout.
    """
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root.parent), str(root.parents[2])]
    import faiss
    import numpy as np
    from astrbot_plugin_livingmemory.core.faiss_async_persist import (
        install_async_persist,
    )
    from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

    from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

    faiss.omp_set_num_threads(1)

    class LocalEmbedding:
        def get_dim(self):
            return args.dimensions

        async def get_embedding(self, text):
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")
            vector = np.random.default_rng(seed).random(
                args.dimensions, dtype=np.float32
            )
            return (vector / np.linalg.norm(vector)).tolist()

    provider = LocalEmbedding()
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix="lmem_benchmark_") as temp:
            db = FaissVecDB(
                str(Path(temp) / "memory.db"),
                str(Path(temp) / "memory.index"),
                provider,
            )
            await db.initialize()
            persister = install_async_persist(db.embedding_storage, 600)
            engine = MemoryEngine(
                db.doc_store_path,
                db,
                config={"recent_memory_count": 0, "search_cache_ttl_seconds": 0},
            )
            await engine.initialize()
            try:
                contents = [f"topic{i // args.sessions}" for i in range(size)]
                now = time.time()
                metadatas = [
                    {
                        "session_id": f"s{i % args.sessions}",
                        "importance": 0.5,
                        "create_time": now,
                        "status": "active",
                    }
                    for i in range(size)
                ]
                ids = await db.document_storage.insert_documents_batch(
                    [str(i) for i in range(size)], contents, metadatas
                )
                embeddings = {
                    text: await provider.get_embedding(text)
                    for text in dict.fromkeys(contents)
                }
                await db.embedding_storage.insert_batch(
                    np.asarray(
                        [embeddings[text] for text in contents], dtype=np.float32
                    ),
                    ids,
                )
                tokens = {
                    text: " ".join(await engine.text_processor.tokenize_async(text))
                    for text in embeddings
                }
                await engine.db_connection.executemany(
                    "INSERT INTO livingmemory_memories_fts(doc_id, content) VALUES (?, ?)",
                    [(doc_id, tokens[text]) for doc_id, text in zip(ids, contents)],
                )
                await engine.db_connection.commit()
                await engine.search_memories(contents[0], 5, "s0")
                latencies, hits, lags = [], [], []
                semaphore = asyncio.Semaphore(args.concurrency)

                async def sample(i):
                    async with semaphore:
                        index = (i * 137) % size
                        start = time.perf_counter()
                        results = await engine.search_memories(
                            contents[index], 5, metadatas[index]["session_id"]
                        )
                        latencies.append((time.perf_counter() - start) * 1000)
                        hits.append(ids[index] in {result.doc_id for result in results})

                async def heartbeat():
                    while True:
                        start = time.perf_counter()
                        await asyncio.sleep(0.01)
                        lags.append(max(0, (time.perf_counter() - start - 0.01) * 1000))

                monitor = asyncio.create_task(heartbeat())
                start = time.perf_counter()
                try:
                    await asyncio.gather(*(sample(i) for i in range(args.queries)))
                finally:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
                elapsed = time.perf_counter() - start
                ordered = sorted(latencies)
                print(
                    json.dumps(
                        {
                            "route": "document hybrid",
                            "embedding": "deterministic local (no network)",
                            "documents": size,
                            "dimensions": args.dimensions,
                            "sessions": args.sessions,
                            "concurrency": args.concurrency,
                            "queries": args.queries,
                            "p50_ms": round(statistics.median(latencies), 2),
                            "p95_ms": round(
                                ordered[
                                    min(len(ordered) - 1, int(len(ordered) * 0.95))
                                ],
                                2,
                            ),
                            "queries_per_second": round(args.queries / elapsed, 2),
                            "recall_at_5": round(sum(hits) / len(hits), 4),
                            "max_event_loop_lag_ms": round(max(lags, default=0), 2),
                            "vector_mib": round(
                                size * args.dimensions * 4 / 1048576, 2
                            ),
                        }
                    )
                )
            finally:
                await engine.close()
                await persister.aclose()
                await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000])
    parser.add_argument("--dimensions", type=int, default=128)
    parser.add_argument("--sessions", type=int, default=100)
    parser.add_argument("--queries", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=4)
    options = parser.parse_args()
    if any(
        value <= 0
        for value in [
            *options.sizes,
            options.dimensions,
            options.sessions,
            options.queries,
            options.concurrency,
        ]
    ):
        parser.error("All benchmark parameters must be positive")
    asyncio.run(benchmark(options))

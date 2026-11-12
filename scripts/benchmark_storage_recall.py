"""Benchmark storage and recall paths against a real LivingMemory database.

Copies a plugin database into a temporary directory with SQLite's backup API
(read-only source, consistent snapshot) and measures the graph-route read path,
the write-path graph maintenance and the atom keyword fallback. These are the
hot spots of the auto-injection recall chain, so before/after numbers are
comparable across code changes.

Usage:
    python scripts/benchmark_storage_recall.py --db <data>/livingmemory.db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASTRBOT_ROOT = PROJECT_ROOT.parents[2]
sys.path[:0] = [str(PROJECT_ROOT.parent), str(ASTRBOT_ROOT)]

from astrbot_plugin_livingmemory.core.models.memory_atom import (  # noqa: E402
    AtomType,
    MemoryAtom,
)
from astrbot_plugin_livingmemory.core.processors.text_processor import (  # noqa: E402
    TextProcessor,
)
from astrbot_plugin_livingmemory.core.retrieval.bm25_retriever import (  # noqa: E402
    BM25Retriever,
)
from astrbot_plugin_livingmemory.core.retrieval.graph_keyword_retriever import (  # noqa: E402
    GraphKeywordRetriever,
)
from astrbot_plugin_livingmemory.storage.atom_store import AtomStore  # noqa: E402
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore  # noqa: E402

MISS_QUERY = "完全不存在的冷门词组zzz"


def copy_database(src: Path, dst: Path) -> None:
    """Snapshot src into dst through SQLite's backup API without locking src."""
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(str(dst))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def sample_queries(db_path: Path, limit: int = 3) -> list[str]:
    """Build deterministic queries from real graph node values."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT canonical_value FROM graph_nodes ORDER BY id LIMIT 24"
        ).fetchall()
    finally:
        conn.close()
    values = [str(row[0]).strip() for row in rows if str(row[0]).strip()]
    if not values:
        return ["记忆"]
    queries = []
    for index in range(limit):
        chunk = values[index * 3 : index * 3 + 3]
        if chunk:
            queries.append(" ".join(chunk))
    return queries or [values[0]]


def topology_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "documents",
                "graph_nodes",
                "graph_edges",
                "graph_entries",
                "graph_entry_nodes",
                "memory_atoms",
            )
        }
    finally:
        conn.close()


def dominant_scope(db_path: Path) -> str | None:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT session_id, COUNT(*) FROM graph_entries "
            "WHERE session_id IS NOT NULL GROUP BY session_id "
            "ORDER BY 2 DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def existing_memory_id(db_path: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT source_memory_id FROM graph_entries ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0]) if row else -1
    finally:
        conn.close()


def emit(case: str, samples: list[float], **extra) -> None:
    payload = {
        "case": case,
        "runs": len(samples),
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        **extra,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


async def measure(case: str, coro_factory, runs: int, **extra) -> None:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        await coro_factory()
        samples.append((time.perf_counter() - started) * 1000)
    emit(case, samples, **extra)


async def benchmark_graph_reads(db_path: Path, runs: int) -> None:
    text_processor = TextProcessor(None, tokenizer="jieba")
    graph_store = GraphStore(str(db_path), cache_reads=True)
    await graph_store.initialize()
    retriever = GraphKeywordRetriever(
        graph_store,
        text_processor,
        config={"graph_expansion_limit": 24, "graph_expansion_hops": 1},
    )
    queries = sample_queries(db_path)
    scope = dominant_scope(db_path)
    try:
        for query in queries:
            await measure(
                "graph.route",
                lambda q=query: retriever.search(q, 10, scope, None),
                runs,
                query=query[:40],
            )

        query = queries[0]
        tokens = await text_processor.tokenize_async(query, remove_stopwords=True)
        fts_query = " OR ".join(
            '"' + token.replace('"', '""') + '"' for token in tokens
        )
        await measure(
            "graph.search_entries_by_bm25",
            lambda: graph_store.search_entries_by_bm25(
                fts_query, limit=15, session_id=scope
            ),
            runs,
        )
        await measure(
            "graph.search_nodes_by_tokens",
            lambda: graph_store.search_nodes_by_tokens(tokens, limit=15),
            runs,
        )
        nodes = await graph_store.search_nodes_by_tokens(tokens, limit=15)
        node_ids = [int(node["id"]) for node in nodes]
        if node_ids:
            await measure(
                "graph.get_entries_for_node_ids",
                lambda: graph_store.get_entries_for_node_ids(
                    node_ids, limit=15, session_id=scope
                ),
                runs,
            )
            await measure(
                "graph.get_neighbor_node_ids",
                lambda: graph_store.get_neighbor_node_ids(node_ids, limit=24),
                runs,
            )
    finally:
        await graph_store.close()


async def benchmark_document_reads(db_path: Path, runs: int) -> None:
    text_processor = TextProcessor(None, tokenizer="jieba")
    bm25 = BM25Retriever(
        str(db_path), text_processor, {"search_cache": None}, cache_reads=True
    )
    await bm25.initialize()
    queries = sample_queries(db_path)
    scope = dominant_scope(db_path)
    try:
        for query in queries:
            await measure(
                "document.bm25_search",
                lambda q=query: bm25.search(q, limit=15, session_id=scope),
                runs,
                query=query[:40],
            )
    finally:
        await bm25.close()


async def benchmark_graph_writes(db_path: Path, runs: int) -> None:
    graph_store = GraphStore(str(db_path), cache_reads=True)
    await graph_store.initialize()
    try:
        # New memory: delete_memory runs before every insert and touches no rows;
        # only the orphan-node sweep makes it expensive.
        await measure(
            "graph.write.new_memory_delete_check",
            lambda: graph_store.delete_memory(-1),
            runs,
        )
        memory_id = existing_memory_id(db_path)
        if memory_id >= 0:
            await measure(
                "graph.write.replace_existing_memory",
                lambda: graph_store.delete_memory(memory_id),
                1,
                memory_id=memory_id,
            )
    finally:
        await graph_store.close()


async def benchmark_atom_fallback(runs: int) -> None:
    atom_count = 20000
    temp_dir = Path(tempfile.mkdtemp(prefix="lmem_bench_atoms_"))
    text_processor = TextProcessor(None, tokenizer="builtin")
    atom_store = AtomStore(
        str(temp_dir / "livingmemory.db"),
        cache_reads=True,
        text_processor=text_processor,
    )
    await atom_store.initialize()
    try:
        batch = 1000
        for start in range(0, atom_count, batch):
            atoms = [
                MemoryAtom(
                    parent_memory_id=index,
                    atom_type=AtomType.FACTUAL,
                    content=f"日常事实内容{index} 关于话题{index % 500}",
                    ttl_days=180,
                )
                for index in range(start, min(start + batch, atom_count))
            ]
            await atom_store.insert_many(atoms)
        await measure(
            "atom.search_fts_miss",
            lambda: atom_store.search_fts(MISS_QUERY, limit=20),
            runs,
            atoms=atom_count,
        )
        await measure(
            "atom.search_fts_hit",
            lambda: atom_store.search_fts("话题42", limit=20),
            runs,
            atoms=atom_count,
        )
    finally:
        await atom_store.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="plugin database path")
    parser.add_argument("--runs", type=int, default=5)
    options = parser.parse_args()
    if not options.db.is_file():
        parser.error(f"database not found: {options.db}")

    temp_dir = Path(tempfile.mkdtemp(prefix="lmem_bench_db_"))
    db_path = temp_dir / "livingmemory.db"
    copy_database(options.db, db_path)
    print(
        json.dumps(
            {
                "case": "environment",
                "source": str(options.db),
                "counts": topology_counts(options.db),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    runs = max(1, options.runs)
    await benchmark_document_reads(db_path, runs)
    await benchmark_graph_reads(db_path, runs)
    await benchmark_graph_writes(db_path, runs)
    await benchmark_atom_fallback(runs)


if __name__ == "__main__":
    asyncio.run(main())

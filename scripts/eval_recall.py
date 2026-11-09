"""Offline, deterministic recall-quality regression harness.

Synthetic Chinese memories + a deterministic char-bigram embedding provider.
The harness drives the real ``MemoryEngine`` document retrieval route
(BM25 + FAISS fusion) with the default relevance threshold
(``min_similarity_for_retrieval=0.45``), then reports ranking metrics.

Scope and limits (intentional):
- The fixture and the embedding are synthetic: this harness only guards
  ranking / threshold / regression behaviour. It does **not** measure the
  semantic quality of a production embedding model, and its numbers must not
  be quoted as real-world recall.
- Graph and atom routes are disabled so the measurement stays on the document
  route; recent-memory slots are disabled so the relevance threshold is
  actually observable.
- Each query is issued with ``k=max(top_k, mrr_k)`` so MRR@10 has ten ranks
  available; recall@5 and nDCG@5 only read the first ``top_k`` ranks.
- No network access, no provider calls, deterministic across platforms.

Usage::

    python scripts/eval_recall.py
    python scripts/eval_recall.py --baseline tests/eval/baseline.json
    python scripts/eval_recall.py --write-baseline tests/eval/baseline.json

Exit code is non-zero when ``--baseline`` is supplied and a higher-is-better
metric drops by more than ``--tolerance`` (or a lower-is-better metric grows
by more than the tolerance).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = PLUGIN_ROOT / "tests" / "fixtures" / "recall_golden.jsonl"

# The evaluation pins the default relevance threshold under review.
MIN_SIMILARITY_FOR_RETRIEVAL = 0.45
DEFAULT_TOP_K = 5
DEFAULT_MRR_K = 10
DEFAULT_TOLERANCE = 0.02

EMBEDDING_DIM = 256
_CJK_RANGE = (0x4E00, 0x9FFF)
_UNIGRAM_WEIGHT = 0.2
_BIGRAM_WEIGHT = 1.0
_ASCII_TOKEN_WEIGHT = 1.0

HIGHER_IS_BETTER = ("recall@5", "mrr@10", "ndcg@5")
LOWER_IS_BETTER = ("negative_injection_rate",)


# ---------------------------------------------------------------------------
# import / logging bootstrap (keep module import cheap for pytest collection)
# ---------------------------------------------------------------------------


def _ensure_import_path() -> None:
    """Expose the AstrBot host and this plugin package to ``sys.path``."""
    candidates = (
        PLUGIN_ROOT,
        PLUGIN_ROOT.parent,
        PLUGIN_ROOT.parents[2],
    )
    for candidate in candidates:
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)


def _silence_host_logs() -> None:
    """Keep stdout parseable: the host logger must not emit INFO lines.

    AstrBot forwards standard logging to loguru, and per-plugin loggers carry
    their own level, so raising the root logger alone is not enough.
    """
    logging.disable(logging.INFO)
    logging.getLogger().setLevel(logging.ERROR)
    for name in list(logging.root.manager.loggerDict):
        logging.getLogger(name).setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# deterministic embedding
# ---------------------------------------------------------------------------


def _hash_bucket(payload: str) -> int:
    digest = hashlib.md5(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little") % EMBEDDING_DIM


def _feature_counts(text: str) -> dict[int, float]:
    """Hash Chinese unigrams/bigrams and ASCII tokens into feature buckets."""
    counts: dict[int, float] = {}
    run: list[str] = []
    for char in text:
        if _CJK_RANGE[0] <= ord(char) <= _CJK_RANGE[1]:
            run.append(char)
            continue
        _accumulate_cjk(counts, run)
        run = []
    _accumulate_cjk(counts, run)

    token: list[str] = []
    for char in text + " ":
        if char.isascii() and (char.isalnum() or char == "_"):
            token.append(char.lower())
            continue
        if token:
            bucket = _hash_bucket("w:" + "".join(token))
            counts[bucket] = counts.get(bucket, 0.0) + _ASCII_TOKEN_WEIGHT
            token = []
    return counts


def _accumulate_cjk(counts: dict[int, float], run: list[str]) -> None:
    if not run:
        return
    for char in run:
        bucket = _hash_bucket("c:" + char)
        counts[bucket] = counts.get(bucket, 0.0) + _UNIGRAM_WEIGHT
    for index in range(len(run) - 1):
        bucket = _hash_bucket("b:" + run[index] + run[index + 1])
        counts[bucket] = counts.get(bucket, 0.0) + _BIGRAM_WEIGHT


def _compute_idf(corpus: list[str]) -> dict[int, float]:
    """Inverse document frequency per hashed feature over the fixture corpus.

    Distinctive fragments (names, project ids, uncommon bigrams) get a high
    weight while boilerplate characters keep a low one, so literal overlap is
    meaningfully stronger than incidental common-character overlap.
    """
    document_count = len(corpus)
    if document_count <= 0:
        return {}
    document_frequency: dict[int, int] = {}
    for text in corpus:
        for bucket in _feature_counts(text):
            document_frequency[bucket] = document_frequency.get(bucket, 0) + 1
    return {
        bucket: math.log((document_count + 1) / (frequency + 1)) + 1.0
        for bucket, frequency in document_frequency.items()
    }


def _embed_with_idf(text: str, idf: dict[int, float]) -> list[float]:
    counts = _feature_counts(text)
    vector = [0.0] * EMBEDDING_DIM
    for bucket, value in counts.items():
        weight = idf.get(bucket, math.log(len(idf) + 2.0) + 1.0 if idf else 1.0)
        if value > 1.0:
            weight *= 1.0 + math.log2(value)
        vector[bucket] = weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


def deterministic_embedding(text: str, corpus: list[str] | None = None) -> list[float]:
    """Corpus-weighted char-bigram/ASCII-token hashed embedding, L2 normalised.

    Texts sharing distinctive Chinese fragments get a higher cosine similarity
    than unrelated texts, which is what the document route needs in order to
    exercise fusion and the relevance threshold deterministically.

    Args:
        text: Text to embed.
        corpus: Optional fixture corpus used to build IDF weights.

    Returns:
        Unit-length embedding vector.
    """
    return _embed_with_idf(text, _compute_idf(corpus or []))


class LocalEmbedding:
    """Deterministic, network-free embedding provider for the eval harness."""

    def __init__(self, corpus: list[str] | None = None):
        self._idf = _compute_idf(corpus or [])

    def get_dim(self) -> int:
        return EMBEDDING_DIM

    async def get_embedding(self, text: str) -> list[float]:
        return _embed_with_idf(text, self._idf)

    async def get_embeddings_batch(
        self, contents: list[str], **kwargs
    ) -> list[list[float]]:
        return [_embed_with_idf(content, self._idf) for content in contents]

    async def get_embedding_with_retry(self, text: str) -> list[float]:
        return _embed_with_idf(text, self._idf)


# ---------------------------------------------------------------------------
# fixture loading
# ---------------------------------------------------------------------------


class FixtureError(ValueError):
    """Raised when the golden fixture is malformed."""


def load_fixture(path: Path) -> tuple[list[dict], list[dict]]:
    """Load and validate the JSONL golden fixture.

    Args:
        path: JSONL file with ``memory`` and ``query`` records.

    Returns:
        ``(memories, queries)`` in file order.

    Raises:
        FixtureError: On malformed records or dangling ``expect`` ids.
    """
    memories: list[dict] = []
    queries: list[dict] = []
    memory_ids: set[str] = set()
    query_ids: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise FixtureError(f"line {line_number}: invalid JSON: {error}") from error
        record_type = record.get("type")
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise FixtureError(f"line {line_number}: missing id")
        if record_type == "memory":
            if record_id in memory_ids:
                raise FixtureError(
                    f"line {line_number}: duplicate memory id {record_id}"
                )
            if not str(record.get("content") or "").strip():
                raise FixtureError(
                    f"line {line_number}: memory {record_id} has no content"
                )
            memory_ids.add(record_id)
            memories.append(record)
        elif record_type == "query":
            if record_id in query_ids:
                raise FixtureError(
                    f"line {line_number}: duplicate query id {record_id}"
                )
            if not str(record.get("query") or "").strip():
                raise FixtureError(f"line {line_number}: query {record_id} is empty")
            query_ids.add(record_id)
            queries.append(record)
        else:
            raise FixtureError(f"line {line_number}: unknown type {record_type!r}")

    for query in queries:
        expect = query.get("expect") or []
        if not isinstance(expect, list):
            raise FixtureError(f"query {query['id']}: expect must be a list")
        missing = [item for item in expect if item not in memory_ids]
        if missing:
            raise FixtureError(f"query {query['id']}: unknown expect ids {missing}")
        if query.get("negative") and expect:
            raise FixtureError(f"query {query['id']}: negative query has expect ids")
        if not query.get("negative") and not expect:
            raise FixtureError(f"query {query['id']}: positive query has no expect ids")

    negatives = [query for query in queries if query.get("negative")]
    if not memories or not queries or len(negatives) < 6:
        raise FixtureError(
            "fixture must contain memories, queries and at least 6 negatives"
        )
    return memories, queries


# ---------------------------------------------------------------------------
# engine harness
# ---------------------------------------------------------------------------


async def _build_engine(workdir: Path, corpus: list[str]):
    """Create a temporary MemoryEngine wired to the document route only."""
    _ensure_import_path()
    _silence_host_logs()

    from astrbot_plugin_livingmemory.core.faiss_async_persist import (
        install_async_persist,
    )
    from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine

    from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

    database_path = workdir / "memory.db"
    index_path = workdir / "memory.index"
    database = FaissVecDB(str(database_path), str(index_path), LocalEmbedding(corpus))
    await database.initialize()
    persister = install_async_persist(database.embedding_storage, 600)
    engine = MemoryEngine(
        str(database_path),
        database,
        config={
            # Document route only: graph/atom routes would add candidate noise
            # that this harness does not measure.
            "graph_memory_enabled": False,
            "atom_enabled": False,
            # Recent-memory slots bypass relevance checks; disable so the
            # min_similarity threshold is the only gate.
            "recent_memory_count": 0,
            "recent_memory_max_age_hours": 0,
            "search_cache_enabled": False,
            "min_similarity_for_retrieval": MIN_SIMILARITY_FOR_RETRIEVAL,
            "min_importance_for_retrieval": 0.0,
            "memory_type_filter": "all",
        },
    )
    await engine.initialize()
    return engine, database, persister


async def _close_engine(engine, database, persister) -> None:
    await engine.close()
    if persister is not None:
        await persister.aclose()
    await database.close()


async def _insert_memories(engine, memories: list[dict]) -> dict[str, int]:
    """Insert fixture memories through the production index writer."""
    now = time.time()
    memory_to_doc_id: dict[str, int] = {}
    for memory in memories:
        age_days = float(memory.get("age_days") or 0)
        created_at = now - age_days * 86400.0
        metadata = {
            "importance": float(memory.get("importance", 0.5)),
            "session_id": str(memory.get("session", "s1")),
            "persona_id": None,
            "status": "active",
            # Direct write path is used (instead of MemoryEngine.add_memory)
            # because fixture ages must survive into ranking metadata.
            "create_time": created_at,
            "last_access_time": created_at,
        }
        doc_id = await engine.index_writer.add_memory(memory["content"], metadata)
        memory_to_doc_id[memory["id"]] = int(doc_id)
    return memory_to_doc_id


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def _dcg(grades: list[int]) -> float:
    score = 0.0
    for index, grade in enumerate(grades):
        if grade <= 0:
            continue
        score += (2**grade - 1) / math.log2(index + 2)
    return score


def _ndcg_at_k(ranked_memory_ids: list[str], graded: dict[str, int], k: int) -> float:
    graded = {str(key): int(value) for key, value in graded.items()}
    actual = [graded.get(memory_id, 0) for memory_id in ranked_memory_ids[:k]]
    ideal = sorted(graded.values(), reverse=True)[:k]
    ideal_score = _dcg(ideal)
    if ideal_score <= 0:
        return 0.0
    return _dcg(actual) / ideal_score


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[position]


async def evaluate(
    memories: list[dict],
    queries: list[dict],
    *,
    top_k: int = DEFAULT_TOP_K,
    mrr_k: int = DEFAULT_MRR_K,
) -> dict:
    """Run the golden fixture through the real document retrieval route.

    Args:
        memories: Validated memory records.
        queries: Validated query records.
        top_k: Cutoff used for recall@k and nDCG@k.
        mrr_k: Cutoff used for MRR.

    Returns:
        Metrics dictionary (JSON-serialisable).
    """
    _ensure_import_path()
    _silence_host_logs()
    # Ranking metrics need at least mrr_k ranks; recall/nDCG keep their own k.
    search_k = max(top_k, mrr_k)

    with tempfile.TemporaryDirectory(prefix="lmem_recall_eval_") as temp:
        corpus = [str(memory["content"]) for memory in memories] + [
            str(query["query"]) for query in queries
        ]
        engine, database, persister = await _build_engine(Path(temp), corpus)
        try:
            memory_to_doc_id = await _insert_memories(engine, memories)
            doc_id_to_memory = {
                doc_id: memory_id for memory_id, doc_id in memory_to_doc_id.items()
            }
            sessions = sorted({str(memory.get("session", "s1")) for memory in memories})
            session_id = sessions[0] if sessions else "s1"

            # Warm up tokenization/index caches so timings reflect steady state.
            await engine.search_memories("预热查询", 1, session_id, None)

            latencies: list[float] = []
            recalls: list[float] = []
            reciprocal_ranks: list[float] = []
            ndcgs: list[float] = []
            negative_hits = 0
            negative_total = 0
            failures: list[dict] = []

            for query in queries:
                started = time.perf_counter()
                results = await engine.search_memories(
                    str(query["query"]), search_k, session_id, None
                )
                latencies.append((time.perf_counter() - started) * 1000.0)

                ranked = [doc_id_to_memory.get(result.doc_id) for result in results]
                ranked = [memory_id for memory_id in ranked if memory_id is not None]
                top_hits = ranked[:top_k]
                expect = [str(item) for item in query.get("expect") or []]

                if query.get("negative"):
                    negative_total += 1
                    if ranked:
                        negative_hits += 1
                        failures.append(
                            {
                                "query": query["id"],
                                "kind": query.get("kind"),
                                "reason": "negative query returned results",
                                "returned": ranked[:top_k],
                            }
                        )
                    continue

                hit = next((item for item in expect if item in top_hits), None)
                recalls.append(1.0 if hit is not None else 0.0)
                if hit is None:
                    failures.append(
                        {
                            "query": query["id"],
                            "kind": query.get("kind"),
                            "reason": "expected memory missing from top-k",
                            "expected": expect,
                            "returned": ranked[:top_k],
                        }
                    )

                rank = next(
                    (
                        position
                        for position, memory_id in enumerate(ranked[:mrr_k], 1)
                        if memory_id in expect
                    ),
                    None,
                )
                reciprocal_ranks.append(1.0 / rank if rank else 0.0)
                ndcgs.append(_ndcg_at_k(ranked, query.get("graded") or {}, top_k))

            latency_p50 = statistics.median(latencies) if latencies else 0.0
            latency_p95 = _percentile(latencies, 0.95)
            return {
                "recall@5": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
                "mrr@10": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4)
                if reciprocal_ranks
                else 0.0,
                "ndcg@5": round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else 0.0,
                "negative_injection_rate": round(negative_hits / negative_total, 4)
                if negative_total
                else 0.0,
                "p50_ms": round(latency_p50, 2),
                "p95_ms": round(latency_p95, 2),
                "positive_queries": len(recalls),
                "negative_queries": negative_total,
                "memories": len(memories),
                "failures": failures,
            }
        finally:
            await _close_engine(engine, database, persister)


def compare_metrics(
    current: dict, baseline: dict, tolerance: float = DEFAULT_TOLERANCE
) -> list[dict]:
    """Compare metrics against a baseline.

    Args:
        current: Metrics from the current run.
        baseline: Recorded baseline metrics.
        tolerance: Allowed degradation for higher-is-better metrics and growth
            for lower-is-better metrics.

    Returns:
        One check entry per metric; ``passed`` is False when the guard fails.
    """
    checks: list[dict] = []
    for metric in HIGHER_IS_BETTER:
        expected = float(baseline.get(metric, 0.0))
        actual = float(current.get(metric, 0.0))
        checks.append(
            {
                "metric": metric,
                "baseline": expected,
                "current": actual,
                "tolerance": tolerance,
                "passed": actual >= expected - tolerance - 1e-12,
            }
        )
    for metric in LOWER_IS_BETTER:
        expected = float(baseline.get(metric, 0.0))
        actual = float(current.get(metric, 0.0))
        checks.append(
            {
                "metric": metric,
                "baseline": expected,
                "current": actual,
                "tolerance": tolerance,
                "passed": actual <= expected + tolerance + 1e-12,
            }
        )
    return checks


async def run_eval(
    fixture_path: Path = DEFAULT_FIXTURE,
    *,
    baseline_path: Path | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    top_k: int = DEFAULT_TOP_K,
    mrr_k: int = DEFAULT_MRR_K,
) -> tuple[dict, list[dict]]:
    """Load the fixture, evaluate it and optionally compare against baseline.

    Args:
        fixture_path: JSONL golden fixture.
        baseline_path: Optional baseline metrics JSON.
        tolerance: Regression tolerance.
        top_k: Recall/nDCG cutoff.
        mrr_k: MRR cutoff.

    Returns:
        ``(metrics, checks)``; ``checks`` is empty without a baseline.
    """
    memories, queries = load_fixture(fixture_path)
    metrics = await evaluate(memories, queries, top_k=top_k, mrr_k=mrr_k)
    checks: list[dict] = []
    if baseline_path is not None:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        checks = compare_metrics(metrics, baseline, tolerance)
    return metrics, checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline deterministic recall-quality regression harness."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--write-baseline", type=Path, default=None)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--mrr-k", type=int, default=DEFAULT_MRR_K)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the harness and print a JSON report to stdout.

    Returns:
        Process exit code: non-zero when a negative query injected a memory or
        when ``--baseline`` regression checks fail.
    """
    args = _parse_args(argv)
    metrics, checks = asyncio.run(
        run_eval(
            args.fixture,
            baseline_path=args.baseline,
            tolerance=args.tolerance,
            top_k=args.top_k,
            mrr_k=args.mrr_k,
        )
    )
    report = {"metrics": metrics, "checks": checks}
    # Negative queries are a hard gate: unrelated queries must inject nothing.
    report["passed"] = metrics["negative_injection_rate"] == 0 and (
        all(check["passed"] for check in checks) if checks else True
    )
    if args.write_baseline is not None:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **metrics}
        args.write_baseline.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

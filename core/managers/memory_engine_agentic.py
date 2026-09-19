"""Beta 自主检索入口：有界候选、相关性策略和请求局部诊断。"""

import asyncio

from astrbot.api import logger

from ..retrieval.hybrid_retriever import HybridResult
from ..retrieval.recall_diagnostics import all_routes_failed, merge_route
from ..retrieval.route_execution import search_route
from ..retrieval.rrf_fusion import BM25Result, VectorResult
from ..retrieval.vector_search import query_embeddings
from ..utils.json_utils import safe_json_dict
from .agentic_recall_types import (
    AgenticRecallOutcome,
    decode_participant_cursor,
    filter_source_time,
    match_basis,
    normalize_doc_ids,
)
from .memory_engine_evidence import MemoryEngineEvidenceMixin
from .memory_engine_participant import MemoryEngineParticipantMixin

_CANDIDATE_LIMIT = 120


class MemoryEngineAgenticMixin(MemoryEngineEvidenceMixin, MemoryEngineParticipantMixin):
    async def search_memories_agentic(
        self,
        *,
        target="conversation",
        query="",
        k=5,
        session_id=None,
        persona_id=None,
        identity_key=None,
        identity_candidates=None,
        start_ts=None,
        end_ts=None,
        exclude_ids=None,
        cursor="",
    ) -> AgenticRecallOutcome:
        query = (query or "").strip()
        if target not in ("conversation", "current_speaker"):
            return AgenticRecallOutcome(
                [], "invalid_query", message="unsupported target"
            )
        if target == "conversation" and not query:
            return AgenticRecallOutcome([], "invalid_query", message="query is empty")
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            return AgenticRecallOutcome(
                [], "invalid_query", message="start_time exceeds end_time"
            )
        if target == "current_speaker":
            try:
                decode_participant_cursor(cursor)
            except ValueError as exc:
                return AgenticRecallOutcome([], "invalid_query", message=str(exc))
        try:
            if target == "current_speaker":
                return await self._agentic_search_by_participant(
                    query=query,
                    k=k,
                    session_id=session_id,
                    persona_id=persona_id,
                    identity_key=identity_key,
                    identity_candidates=identity_candidates or [],
                    start_ts=start_ts,
                    end_ts=end_ts,
                    exclude_ids=exclude_ids,
                    cursor=cursor,
                )
            return await self._agentic_search_conversation(
                query=query,
                k=k,
                session_id=session_id,
                persona_id=persona_id,
                start_ts=start_ts,
                end_ts=end_ts,
                exclude_ids=exclude_ids,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[MemoryEngine] Beta 自主检索失败", exc_info=True)
            return AgenticRecallOutcome(
                [], "error", message="memory retrieval unavailable"
            )

    async def _retrieve_agentic_candidates(
        self,
        query,
        pool,
        session_id,
        persona_id,
        diagnostics,
    ):
        retriever = self.dual_route_retriever or self.hybrid_retriever
        if retriever is None:
            raise RuntimeError("memory retriever is not ready")
        return await retriever.search(
            query,
            pool,
            session_id,
            persona_id,
            diagnostics=diagnostics,
            relevance_only=True,
        )

    async def _agentic_search_conversation(
        self,
        *,
        query,
        k,
        session_id,
        persona_id,
        start_ts,
        end_ts,
        exclude_ids,
    ):
        excluded = normalize_doc_ids(exclude_ids)
        pool = min(max(k * 3, k + len(excluded)), 60)
        cache = {}
        token = query_embeddings.set(cache)
        unknown_time = 0
        expanded = False
        try:
            while True:
                diagnostics = {}
                results = await self._retrieve_agentic_candidates(
                    query, pool, session_id, persona_id, diagnostics
                )
                saturated = len(results) >= pool
                results = await self._attach_agentic_atoms(
                    results, query, pool, session_id, persona_id, diagnostics
                )
                saturated = saturated or bool(diagnostics.get("candidate_limited"))
                # 图索引可能落后于父文档，合并后的实际记忆仍须满足本次访问范围。
                results = [
                    result
                    for result in results
                    if (
                        session_id is None
                        or result.metadata.get("session_id") == session_id
                    )
                    and (
                        persona_id is None
                        or result.metadata.get("persona_id") == persona_id
                    )
                ]
                results = await self._apply_agentic_atom_policy(results)
                results = self._filter_by_retrieval_policy(results)
                results = [r for r in results if r.doc_id not in excluded]
                results, unknown = filter_source_time(results, start_ts, end_ts)
                unknown_time = max(unknown_time, unknown)
                if (
                    len(results) > k
                    or not saturated
                    or pool >= _CANDIDATE_LIMIT
                    or all_routes_failed(diagnostics)
                ):
                    break
                pool = min(pool * 3, _CANDIDATE_LIMIT)
                expanded = True
        finally:
            query_embeddings.reset(token)
            for task in cache.values():
                if not task.done():
                    task.cancel()
            if cache:
                await asyncio.gather(*cache.values(), return_exceptions=True)

        errors = diagnostics.get("route_errors") or {}
        page = results[:k]
        incomplete = saturated and len(results) <= k
        messages = []
        if errors:
            messages.append("retrieval_routes_failed: " + ", ".join(sorted(errors)))
        if incomplete:
            messages.append(
                "candidate coverage is limited; refine the query or filters"
            )
        if unknown_time:
            messages.append("memories without known source times were excluded")
        self._track_agentic_access(page)
        return AgenticRecallOutcome(
            results=page,
            status="success" if page else ("error" if errors else "no_candidates"),
            has_more=len(results) > k or incomplete,
            match_basis={r.doc_id: match_basis(r) for r in page},
            message="; ".join(messages),
            partial=bool(errors),
            coverage="limited"
            if incomplete or unknown_time or errors
            else ("expanded" if expanded else ""),
        )

    async def _attach_agentic_atoms(
        self,
        results,
        query,
        pool,
        session_id,
        persona_id,
        diagnostics,
    ):
        if self.atom_retriever is None:
            return results
        atom_diagnostics = {}
        atoms, error = await search_route(
            "atoms",
            self.atom_retriever.search(
                query,
                min(pool * 2, 240),
                session_id,
                persona_id,
                relevance_only=True,
                diagnostics=atom_diagnostics,
            ),
        )
        merge_route(diagnostics, "atoms", atom_diagnostics, error)
        if not atoms:
            return results
        scores = {}
        for atom in atoms:
            scores[atom.parent_memory_id] = max(
                scores.get(atom.parent_memory_id, 0), atom.base_score
            )
        documents = {r.doc_id: r for r in results}
        missing = [doc_id for doc_id in scores if doc_id not in documents]
        if missing:
            loaded = await self.faiss_db.document_storage.get_documents(
                metadata_filters={}, ids=missing, limit=len(missing)
            )
            for document in loaded:
                metadata = safe_json_dict(document.get("metadata"))
                if session_id is not None and metadata.get("session_id") != session_id:
                    continue
                if persona_id is not None and metadata.get("persona_id") != persona_id:
                    continue
                doc_id = int(document["id"])
                documents[doc_id] = HybridResult(
                    doc_id=doc_id,
                    final_score=0.0,
                    rrf_score=0.0,
                    bm25_score=None,
                    vector_score=None,
                    content=str(document["text"]),
                    metadata=metadata,
                    score_breakdown={},
                )
        atom_ids = sorted(
            (doc_id for doc_id in scores if doc_id in documents),
            key=lambda doc_id: scores[doc_id],
            reverse=True,
        )
        # 用已有 RRF 合并各路线的排名，不能把原子分数直接与原始 RRF 分数比较。
        fused = self.rrf_fusion.fuse(
            [
                BM25Result(r.doc_id, r.final_score, r.content, r.metadata)
                for r in results
            ],
            [
                VectorResult(
                    doc_id,
                    scores[doc_id],
                    documents[doc_id].content,
                    documents[doc_id].metadata,
                )
                for doc_id in atom_ids
            ],
            top_k=len(documents),
        )
        maximum = max((r.rrf_score for r in fused), default=1.0) or 1.0
        combined = []
        for item in fused:
            original = documents[item.doc_id]
            breakdown = dict(original.score_breakdown or {})
            if item.doc_id in scores:
                breakdown["atom_score"] = scores[item.doc_id]
            relevance = item.rrf_score / maximum
            breakdown["agentic_relevance"] = relevance
            combined.append(
                HybridResult(
                    doc_id=item.doc_id,
                    final_score=relevance,
                    rrf_score=item.rrf_score,
                    bm25_score=original.bm25_score,
                    vector_score=original.vector_score,
                    content=original.content,
                    metadata=original.metadata,
                    score_breakdown=breakdown,
                )
            )
        return combined

    def _track_agentic_access(self, results):
        if results:
            self._create_tracked_task(
                self._update_access_times_internal(
                    [r.doc_id for r in results],
                    [
                        atom_id
                        for r in results
                        for atom_id in r.metadata.get("retrieved_atom_ids", [])
                    ],
                )
            )

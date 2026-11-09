"""Beta 自主检索入口：有界候选、相关性策略、人物回忆与证据读取。"""

import asyncio
import time
from dataclasses import replace
from typing import Any

from astrbot.api import logger

from ..memory_scope import (
    adapter_sql,
    memory_scope_sql,
    memory_scope_visible,
    merge_memory_scopes,
)
from ..retrieval.hybrid_retriever import HybridResult
from ..retrieval.recall_diagnostics import all_routes_failed, merge_route
from ..retrieval.recall_trace import current_trace_context, snapshot_candidates
from ..retrieval.route_execution import search_route
from ..retrieval.rrf_fusion import BM25Result, VectorResult
from ..retrieval.vector_search import (
    query_embeddings,
    retrieve_documents,
)
from ..utils.json_utils import safe_json_dict
from .agentic_recall_types import (
    AgenticRecallOutcome,
    decode_participant_cursor,
    encode_participant_cursor,
    escape_like,
    filter_source_time,
    match_basis,
    normalize_doc_ids,
    parse_source_timestamp,
    participant_time,
)

_CANDIDATE_LIMIT = 120
_PARTICIPANT_PAGE_SIZE = 200
_SCAN_LIMIT = 2000
_TIME_SQL = (
    "COALESCE(lmem_recall_time(json_extract(metadata, '$.source_time_start')), 0)"
)


class MemoryEngineAgenticMixin:
    async def search_memories_agentic(
        self,
        *,
        target="conversation",
        query="",
        k=5,
        session_id=None,
        persona_id=None,
        extra_scopes: list[str] | None = None,
        identity_key=None,
        identity_candidates=None,
        start_ts=None,
        end_ts=None,
        exclude_ids=None,
        cursor="",
    ) -> AgenticRecallOutcome:
        """Beta 自主检索入口；开启召回追踪时记录一次 agentic trace。

        Args:
            target: ``conversation`` 或 ``current_speaker``。
            query: 查询文本。
            k: 返回条数。
            session_id: 会话作用域。
            persona_id: 人格过滤。
            identity_key: 当前说话人稳定身份键。
            identity_candidates: 身份候选值。
            start_ts: 来源时间下界。
            end_ts: 来源时间上界。
            exclude_ids: 需要排除的记忆 id。
            cursor: 人物回忆分页游标。

        Returns:
            ``AgenticRecallOutcome``；失败时由内部转换为错误状态。
        """
        recorder = getattr(self, "trace_recorder", None)
        trace = None
        if recorder is not None and recorder.enabled:
            trace = self._begin_recall_trace(
                recorder, str(query or ""), session_id, persona_id
            )
            if trace is not None:
                trace.agentic = True
                context = current_trace_context()
                if context is not None and context.budget_truncated:
                    trace.budget_truncated = True
        try:
            outcome = await self._search_memories_agentic_impl(
                target=target,
                query=query,
                k=k,
                session_id=session_id,
                persona_id=persona_id,
                extra_scopes=extra_scopes,
                identity_key=identity_key,
                identity_candidates=identity_candidates,
                start_ts=start_ts,
                end_ts=end_ts,
                exclude_ids=exclude_ids,
                cursor=cursor,
            )
        except asyncio.CancelledError:
            if trace is not None and recorder is not None:
                trace.notes.append("cancelled")
                recorder.commit(trace)
            raise
        if trace is not None and recorder is not None:
            failed = outcome.status == "error"
            recorder.route(
                trace,
                "agentic",
                candidates=len(outcome.results),
                ok=not failed,
                error=outcome.message[:120] if failed else None,
            )
            trace.candidates = snapshot_candidates(outcome.results)
            trace.notes.append(f"agentic_status:{outcome.status}")
            if outcome.partial:
                trace.notes.append("partial")
            if outcome.has_more:
                trace.notes.append("has_more")
            recorder.commit(trace)
        return outcome

    async def _search_memories_agentic_impl(
        self,
        *,
        target="conversation",
        query="",
        k=5,
        session_id=None,
        persona_id=None,
        extra_scopes: list[str] | None = None,
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
        scope_filter = merge_memory_scopes(session_id, extra_scopes)
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
                    session_id=scope_filter,
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
                session_id=scope_filter,
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
                    if memory_scope_visible(result.metadata, session_id)
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
            loaded = await retrieve_documents(
                self.faiss_db, metadata_filters={}, ids=missing, limit=len(missing)
            )
            for document in loaded:
                metadata = safe_json_dict(document.get("metadata"))
                if not memory_scope_visible(metadata, session_id):
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

    async def _agentic_search_by_participant(
        self,
        *,
        query: str,
        k: int,
        session_id: str | None,
        persona_id: str | None,
        identity_key: str | None,
        identity_candidates: list[str],
        start_ts: float | None,
        end_ts: float | None,
        exclude_ids: list[int] | None,
        cursor: str,
    ) -> AgenticRecallOutcome:
        if not identity_key:
            return AgenticRecallOutcome(
                results=[],
                status="identity_unavailable",
                message="current speaker identity is unavailable",
            )
        if self.db_connection is None:
            raise RuntimeError("memory database is not ready")
        cursor_pos = decode_participant_cursor(cursor)
        if (
            getattr(self, "_agentic_time_function_connection", None)
            is not self.db_connection
        ):
            await self.db_connection.create_function(
                "lmem_recall_time", 1, parse_source_timestamp, deterministic=True
            )
            self._agentic_time_function_connection = self.db_connection

        conditions = [
            "COALESCE(json_extract(metadata, '$.status'), 'active') = 'active'"
        ]
        params: list[Any] = []
        scope_clause, scope_params = memory_scope_sql(
            "json_extract(metadata, '$.session_id')", session_id
        )
        if scope_clause:
            conditions.append(scope_clause)
            params.extend(scope_params)
        if persona_id is not None:
            conditions.append("json_extract(metadata, '$.persona_id') = ?")
            params.append(persona_id)
        ownership, ownership_params = adapter_sql("metadata")
        conditions.append(ownership)
        params.extend(ownership_params)
        person_conditions, person_params = self._participant_conditions(
            identity_key, identity_candidates
        )
        conditions.append("(" + " OR ".join(person_conditions) + ")")
        params.extend(person_params)
        if query:
            tokens = list(
                dict.fromkeys(await self.text_processor.tokenize_async(query))
            )
            if not tokens:
                return AgenticRecallOutcome(
                    results=[],
                    status="invalid_query",
                    message="no usable topic keywords",
                )
            has_atoms = await self._agentic_has_atom_table()
            topic_sql, topic_params = self._participant_topic_conditions(
                tokens[:20], has_atoms
            )
            conditions.append(topic_sql)
            params.extend(topic_params)
        excluded = normalize_doc_ids(exclude_ids)
        if excluded:
            conditions.append(f"id NOT IN ({','.join('?' for _ in excluded)})")
            params.extend(sorted(excluded))
        where_sql = " AND ".join(conditions)
        matched: list[HybridResult] = []
        bases: dict[int, str] = {}
        cursors: dict[int, str] = {}
        scanned = 0
        unknown_time = 0
        exhausted = False
        last_scanned = None

        # 多读一个有效候选，用它判断还有更多，而不是提前消耗整批 SQL 行。
        while len(matched) <= k and scanned < _SCAN_LIMIT:
            sql, run_params = self._participant_page_sql(where_sql, params, cursor_pos)
            limit = min(_PARTICIPANT_PAGE_SIZE, _SCAN_LIMIT - scanned)
            async with self.db_connection.execute(
                sql + f" ORDER BY {_TIME_SQL} DESC, id DESC LIMIT ?",
                (*run_params, limit),
            ) as statement:
                rows = await statement.fetchall()
            if not rows:
                exhausted = True
                break
            scanned += len(rows)
            exhausted = len(rows) < limit
            batch = []
            for row in rows:
                metadata = safe_json_dict(row["metadata"])
                doc_id = int(row["id"])
                last_scanned = (participant_time(metadata), doc_id)
                cursors[doc_id] = encode_participant_cursor(*last_scanned)
                bases[doc_id] = self._participant_basis(
                    metadata, identity_key, identity_candidates
                )
                batch.append(
                    HybridResult(
                        doc_id=doc_id,
                        final_score=1.0,
                        rrf_score=0.0,
                        bm25_score=None,
                        vector_score=None,
                        content=str(row["text"] or ""),
                        metadata=metadata,
                        score_breakdown={"participant_match": 1.0},
                    )
                )
            batch = await self._apply_agentic_atom_policy(batch)
            batch = self._filter_by_retrieval_policy(batch)
            batch, unknown = filter_source_time(batch, start_ts, end_ts)
            unknown_time += unknown
            matched.extend(batch)
            cursor_pos = last_scanned
            if exhausted:
                break

        page = matched[:k]
        has_more = len(matched) > k or not exhausted
        anchor = (
            (participant_time(page[-1].metadata), page[-1].doc_id)
            if page
            else last_scanned
        )
        page_bases = {r.doc_id: bases[r.doc_id] for r in page}
        identities = set(page_bases.values())
        limited = not exhausted and len(matched) <= k
        self._track_agentic_access(page)
        return AgenticRecallOutcome(
            results=page,
            status="success" if page else "no_candidates",
            has_more=has_more,
            next_cursor=encode_participant_cursor(*anchor)
            if has_more and anchor
            else "",
            match_basis=page_bases,
            identity_status=next(iter(identities))
            if len(identities) == 1
            else ("mixed" if identities else ""),
            result_cursors={r.doc_id: cursors[r.doc_id] for r in page},
            coverage="limited" if limited or unknown_time else "",
            message="some source times are unknown"
            if unknown_time
            else (
                "participant scan limit reached; continue with next_cursor"
                if limited
                else ""
            ),
        )

    @staticmethod
    def _participant_page_sql(where_sql, params, cursor_pos):
        sql = "SELECT id, text, metadata FROM documents WHERE " + where_sql
        run_params = list(params)
        if cursor_pos is not None:
            timestamp, doc_id = cursor_pos
            sql += f" AND ({_TIME_SQL} < ? OR ({_TIME_SQL} = ? AND id < ?))"
            run_params.extend([timestamp, timestamp, doc_id])
        return sql, run_params

    @staticmethod
    def _participant_topic_conditions(tokens: list[str], has_atoms: bool):
        clauses = []
        params = []
        # 每个词都约束当前可见事实；不同事实中的词可以共同描述同一段经历。
        for token in tokens:
            pattern = f"%{escape_like(token)}%"
            if has_atoms:
                clauses.append(
                    "(EXISTS (SELECT 1 FROM memory_atoms AS ma "
                    "WHERE ma.parent_memory_id = documents.id AND ma.status = 'active' "
                    "AND ma.expires_at > ? AND ma.content LIKE ? ESCAPE '\\') "
                    "OR (NOT EXISTS (SELECT 1 FROM memory_atoms AS ma "
                    "WHERE ma.parent_memory_id = documents.id) "
                    "AND text LIKE ? ESCAPE '\\'))"
                )
                params.extend([time.time(), pattern, pattern])
            else:
                clauses.append("text LIKE ? ESCAPE '\\'")
                params.append(pattern)
        return "(" + " AND ".join(clauses) + ")", params

    @staticmethod
    def _participant_conditions(identity_key: str, identity_candidates: list[str]):
        conditions = []
        params = []
        for key in dict.fromkeys([identity_key, *identity_candidates]):
            if not key:
                continue
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each("
                "json_extract(metadata, '$.participant_identities')) AS je "
                "WHERE json_extract(je.value, '$.identity_key') = ?)"
            )
            params.append(key)
        for name in dict.fromkeys(identity_candidates):
            if not name:
                continue
            conditions.extend(
                [
                    "EXISTS (SELECT 1 FROM json_each("
                    "json_extract(metadata, '$.participants')) AS je WHERE je.value = ?)",
                    "text LIKE ? ESCAPE '\\'",
                ]
            )
            params.extend([name, f"%{escape_like(name)}%"])
        return conditions, params

    @staticmethod
    def _participant_basis(metadata, identity_key, identity_candidates):
        accepted = {identity_key, *identity_candidates}
        identities = metadata.get("participant_identities")
        if isinstance(identities, list) and any(
            isinstance(item, dict) and item.get("identity_key") in accepted
            for item in identities
        ):
            return "identity_confirmed"
        participants = metadata.get("participants")
        if isinstance(participants, list) and any(
            str(item or "").strip() in accepted for item in participants
        ):
            return "legacy_candidate"
        return "text_legacy"

    async def _agentic_has_atom_table(self) -> bool:
        # 原子表在引擎初始化时创建，结果可按实例缓存；避免每次召回都查 sqlite_master。
        cached = getattr(self, "_atom_table_checked", None)
        if cached is not None:
            return cached
        if self.db_connection is None:
            raise RuntimeError("memory database is not ready")
        async with self.db_connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_atoms'"
        ) as cursor:
            present = await cursor.fetchone() is not None
        self._atom_table_checked = present
        return present

    async def _agentic_atom_rows(self, memory_ids: list[int]) -> dict[int, list]:
        """按候选批量取状态；只有确认不存在表时才按普通旧文档处理。"""
        if not memory_ids or not await self._agentic_has_atom_table():
            return {}
        grouped: dict[int, list] = {}
        ids = list(dict.fromkeys(memory_ids))
        for start in range(0, len(ids), 500):
            batch = ids[start : start + 500]
            async with self.db_connection.execute(
                "SELECT id, parent_memory_id, content, status, expires_at "
                "FROM memory_atoms WHERE parent_memory_id IN "
                f"({','.join('?' for _ in batch)}) ORDER BY id",
                batch,
            ) as cursor:
                for row in await cursor.fetchall():
                    grouped.setdefault(int(row[1]), []).append(row)
        return grouped

    @staticmethod
    def _agentic_live_rows(rows: list) -> list:
        now = time.time()
        return [row for row in rows if row[3] == "active" and row[4] > now]

    async def _apply_agentic_atom_policy(
        self, results: list[HybridResult]
    ) -> list[HybridResult]:
        grouped = await self._agentic_atom_rows([r.doc_id for r in results])
        filtered = []
        for result in results:
            rows = grouped.get(result.doc_id)
            if rows is None and not result.metadata.get("atom_types"):
                filtered.append(result)
                continue
            live = self._agentic_live_rows(rows or [])
            if live:
                filtered.append(
                    replace(
                        result,
                        content="\n".join(dict.fromkeys(str(row[2]) for row in live)),
                        metadata={
                            **result.metadata,
                            "retrieved_atom_ids": [int(row[0]) for row in live],
                        },
                    )
                )
        return filtered

    async def resolve_live_facts(
        self, memory_id: int, content: str, metadata: dict[str, Any]
    ) -> list[str] | None:
        grouped = await self._agentic_atom_rows([memory_id])
        rows = grouped.get(memory_id)
        if rows is None and not metadata.get("atom_types"):
            return [line for line in content.split("\n") if line.strip()]
        live = self._agentic_live_rows(rows or [])
        return list(dict.fromkeys(str(row[2]) for row in live)) if live else None

    async def count_memory_atoms(self, memory_id: int) -> int:
        if not await self._agentic_has_atom_table():
            return 0
        async with self.db_connection.execute(
            "SELECT COUNT(*) FROM memory_atoms WHERE parent_memory_id = ?",
            (int(memory_id),),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0])

    async def get_memory_for_recall(self, memory_id: int) -> dict | None:
        """Beta 严格读取，普通存储异常不转换为不存在；旧 get_memory 不变。"""
        documents = await retrieve_documents(
            self.faiss_db, metadata_filters={}, ids=[int(memory_id)], limit=1
        )
        if not documents:
            return None
        document = documents[0]
        return {
            "id": int(document["id"]),
            "text": str(document.get("text") or ""),
            "metadata": safe_json_dict(document.get("metadata")),
        }

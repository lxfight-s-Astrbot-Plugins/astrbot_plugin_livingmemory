"""Beta 人物回忆：SQL 前置身份/主题条件，按来源时间稳定续查。"""

import time
from typing import Any

from ..retrieval.hybrid_retriever import HybridResult
from ..utils.json_utils import safe_json_dict
from .agentic_recall_types import (
    AgenticRecallOutcome,
    decode_participant_cursor,
    encode_participant_cursor,
    escape_like,
    filter_source_time,
    normalize_doc_ids,
    parse_source_timestamp,
    participant_time,
)

_PARTICIPANT_PAGE_SIZE = 200
_SCAN_LIMIT = 2000
_TIME_SQL = (
    "COALESCE(lmem_recall_time(json_extract(metadata, '$.source_time_start')), 0)"
)


class MemoryEngineParticipantMixin:
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
        for key, value in (("session_id", session_id), ("persona_id", persona_id)):
            if value is not None:
                conditions.append(f"json_extract(metadata, '$.{key}') = ?")
                params.append(value)
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

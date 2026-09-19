"""Beta 专用证据读取：严格错误传播与不依赖功能开关的生命周期检查。"""

import time
from dataclasses import replace
from typing import Any

from ..retrieval.hybrid_retriever import HybridResult
from ..utils.json_utils import safe_json_dict


class MemoryEngineEvidenceMixin:
    async def _agentic_has_atom_table(self) -> bool:
        if self.db_connection is None:
            raise RuntimeError("memory database is not ready")
        async with self.db_connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_atoms'"
        ) as cursor:
            return await cursor.fetchone() is not None

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

    async def get_memory_atom_lifecycle(self, memory_id: int) -> dict | None:
        rows = (await self._agentic_atom_rows([memory_id])).get(memory_id)
        if not rows:
            return None
        active = len(self._agentic_live_rows(rows))
        return {"total": len(rows), "active": active, "hidden": len(rows) - active}

    async def get_memory_for_recall(self, memory_id: int) -> dict | None:
        """Beta 严格读取，普通存储异常不转换为不存在；旧 get_memory 不变。"""
        documents = await self.faiss_db.document_storage.get_documents(
            metadata_filters={}, ids=[int(memory_id)], limit=1
        )
        if not documents:
            return None
        document = documents[0]
        return {
            "id": int(document["id"]),
            "text": str(document.get("text") or ""),
            "metadata": safe_json_dict(document.get("metadata")),
        }

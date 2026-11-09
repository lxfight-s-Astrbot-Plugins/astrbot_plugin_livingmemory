"""记忆写入、更新、删除与来源存储。"""

import asyncio
import json
import re
import time
from collections import OrderedDict
from typing import Any

import aiosqlite

from astrbot.api import logger

from ...storage.atom_store import AtomStore
from ..memory_scope import (
    current_adapter_access,
    infer_adapter_id,
)
from ..processors.atom_classifier import classify_atoms
from ..retrieval.hybrid_retriever import HybridResult
from ..utils.json_utils import safe_json_dict
from ..utils.number_utils import clamp_float
from .agentic_recall_types import AgenticRecallOutcome as AgenticRecallOutcome

# 合并后的正文上限：两段摘要拼接后的长度上限，超过则只保留更完整的一段，
# 避免同一记忆在多轮合并后无限膨胀（会放大 embedding/FTS 写入成本）。
_MERGED_CONTENT_MAX_CHARS = 1200
# 句子级切分：中英文句末标点与换行；保留标点由正则后行断言完成。
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;\n])")


class MemoryEngineCrudMixin:
    """MemoryEngine 的 MemoryEngineCrudMixin 分组。

    宿主共享状态由 ``MemoryEngine`` 独占持有，本模块只通过 self 访问。
    """

    # 宿主共享状态契约（由 MemoryEngine.__init__ / initialize 赋值）
    db_connection: aiosqlite.Connection | None
    config: dict[str, Any]
    db_path: str
    faiss_db: Any
    atom_enabled: bool
    atom_store: AtomStore | None
    atom_retriever: Any | None
    vector_retriever: Any | None
    bm25_retriever: Any | None
    hybrid_retriever: Any | None
    index_writer: Any | None
    dual_route_retriever: Any | None
    graph_memory_manager: Any | None
    graph_store: Any | None
    _search_cache_enabled: bool
    _search_cache_ttl: float
    _search_cache_max_size: int
    _search_cache_generation: int
    _search_cache: OrderedDict[tuple[Any, ...], tuple[float, list[HybridResult]]]
    _access_update_pending: set[int]
    _access_atom_update_pending: set[int]
    _access_flush_task: asyncio.Task | None
    index_maintenance_status: dict[str, Any]

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
        preserve_create_time: bool = False,
        source_messages: list[dict[str, Any]] | None = None,
        *,
        embedding: list[float] | None = None,
    ) -> int:
        """添加新记忆。

        Args:
            content: 记忆内容。
            session_id: 会话 ID（保存完整 unified_msg_origin）。
            persona_id: 人格 ID。
            importance: 重要性（0-1）。
            metadata: 额外元数据。
            atoms: 记忆原子列表。
            preserve_create_time: 物理替换时保留原记忆的时间轴位置。
            source_messages: 原文消息。
            embedding: 可选预计算向量（写入路径复用去重阶段的 embedding）。

        Returns:
            int: 记忆 ID（doc_id）。

        Raises:
            ValueError: 内容为空。
            RuntimeError: 索引写入器未初始化。
        """
        if not content or not content.strip():
            raise ValueError("记忆内容不能为空")
        if self.index_writer is None:
            raise RuntimeError("索引写入器未初始化")

        full_metadata = self._build_memory_metadata(
            session_id=session_id,
            persona_id=persona_id,
            importance=importance,
            metadata=metadata,
            atoms=atoms,
            preserve_create_time=preserve_create_time,
            source_messages=source_messages,
        )
        access = current_adapter_access.get()
        full_metadata["adapter_id"] = full_metadata.get("adapter_id") or (
            access.adapter_id
            if access is not None
            else infer_adapter_id(
                full_metadata,
                source_messages or [],
                self.config.get("adapter_instances"),
            )
        )
        doc_id = await self.index_writer.add_memory(
            content, full_metadata, embedding=embedding
        )
        await self._write_memory_atoms(
            doc_id, session_id, persona_id, atoms, full_metadata.get("adapter_id")
        )
        await self._index_memory_graph(doc_id, content, full_metadata, atoms)
        await self._persist_memory_source(doc_id, source_messages)
        self._invalidate_search_cache()
        return doc_id

    def _build_memory_metadata(
        self,
        *,
        session_id: str | None,
        persona_id: str | None,
        importance: float,
        metadata: dict[str, Any] | None,
        atoms: list | None,
        preserve_create_time: bool,
        source_messages: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """合并调用方元数据与时间/原子类型字段，返回待写入的完整元数据。"""
        current_time = time.time()
        full_metadata: dict[str, Any] = {
            "session_id": session_id,  # 保存完整的 unified_msg_origin
            "persona_id": persona_id,  # 保存完整的 persona_id
            "importance": max(0.0, min(1.0, importance)),  # 限制在0-1范围
            "create_time": current_time,
            "last_access_time": current_time,
            "status": "active",
        }

        # 先合并外部 metadata，再确保时间字段不被覆盖
        if metadata:
            full_metadata.update(metadata)
        if source_messages:
            full_metadata["has_source"] = True
            full_metadata["source_message_count"] = len(source_messages)
        if atoms:
            full_metadata["atom_types"] = sorted(
                {
                    getattr(getattr(atom, "atom_type", None), "value", "unknown")
                    for atom in atoms
                }
            )

        # 普通新增使用当前时间；物理替换保留原记忆的时间轴位置。
        preserved_create_time = None
        if preserve_create_time and metadata:
            try:
                preserved_create_time = float(metadata.get("create_time"))
            except (TypeError, ValueError):
                preserved_create_time = None
        full_metadata["create_time"] = (
            preserved_create_time if preserved_create_time is not None else current_time
        )
        full_metadata["last_access_time"] = current_time
        return full_metadata

    async def _write_memory_atoms(
        self,
        doc_id: int,
        session_id: str | None,
        persona_id: str | None,
        atoms: list | None,
        adapter_id: str | None = None,
    ) -> None:
        """Insert owned atoms once and propagate storage failures to the caller."""
        if not atoms or self.atom_store is None or not self.atom_enabled:
            return
        prepared_atoms = []
        for atom in atoms:
            atom.session_id = atom.session_id or session_id
            atom.persona_id = atom.persona_id or persona_id
            atom.metadata["adapter_id"] = adapter_id
            atom.parent_memory_id = doc_id
            prepared_atoms.append(atom)
        await self.atom_store.insert_many(prepared_atoms)

    async def _index_memory_graph(
        self,
        doc_id: int,
        content: str,
        full_metadata: dict[str, Any],
        atoms: list | None,
    ) -> None:
        """建立图记忆索引；失败只记录日志，由重建对账。"""
        if self.graph_memory_manager is None:
            return
        try:
            indexed = await self.graph_memory_manager.index_memory(
                doc_id, content, full_metadata, atoms
            )
            if indexed is False:
                raise RuntimeError("Graph indexing deferred until rebuild completes")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[MemoryEngine] 图记忆索引失败 (memory_id={doc_id})",
                exc_info=True,
            )

    async def _persist_memory_source(
        self,
        doc_id: int,
        source_messages: list[dict[str, Any]] | None,
    ) -> None:
        """保存原文消息；失败时回滚刚写入的记忆。"""
        if not source_messages:
            return
        try:
            await self.save_memory_source(doc_id, source_messages)
        except asyncio.CancelledError:
            await asyncio.shield(self.delete_memory(doc_id))
            raise
        except Exception:
            if not await self.delete_memory(doc_id):
                logger.error(
                    f"[MemoryEngine] 原文写入失败且记忆回滚失败 (memory_id={doc_id})"
                )
            raise

    async def save_memory_source(
        self, memory_id: int, source_messages: list[dict[str, Any]]
    ) -> None:
        """Persist source messages outside retrieval metadata and indexes."""
        now = time.time()
        async with self._write_transaction() as connection:
            await connection.execute(
                """
                INSERT INTO memory_sources(memory_id, source_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    source_json = excluded.source_json,
                    updated_at = excluded.updated_at
                """,
                (
                    int(memory_id),
                    json.dumps(source_messages, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
    ) -> bool:
        """
        更新记忆（确保多数据库同步）

        支持更新内容、重要性、元数据等。采用不同策略：
        - 内容更新：先创建后删除（避免数据丢失）+ 全库同步
        - 元数据更新：三库同步更新

        Args:
            memory_id: 记忆ID
            updates: 更新字典,可包含:
                - content: 新内容 (触发完整重建)
                - importance: 新重要性
                - metadata: 元数据更新

        Returns:
            bool: 是否更新成功
        """
        # 获取当前记忆
        memory = await self.get_memory(memory_id)
        if not memory:
            logger.error(f"[更新] 记忆不存在 (memory_id={memory_id})")
            return False

        # 解析 metadata（可能是JSON字符串）
        current_metadata = safe_json_dict(memory.get("metadata", {}))

        # 处理内容更新 (需要重建所有索引)
        if "content" in updates:
            new_content = updates["content"]
            if not new_content or not new_content.strip():
                return False

            try:
                importance = clamp_float(
                    updates.get("importance", current_metadata.get("importance", 0.5)),
                    default=0.5,
                )

                # 构建新元数据
                new_metadata = current_metadata.copy()
                metadata_patch = updates.get("metadata")
                if isinstance(metadata_patch, dict):
                    new_metadata.update(metadata_patch)
                new_metadata["updated_at"] = time.time()
                new_metadata["previous_id"] = memory_id  # 记录旧ID
                new_metadata["importance"] = importance

                # 【改进】先创建新记忆，再删除旧记忆（避免数据丢失）
                logger.info(f"[更新] 开始内容更新流程 (old_id={memory_id})")

                new_memory_id = await self.replace_memory(
                    memory_id,
                    content=new_content,
                    importance=importance,
                    metadata=new_metadata,
                )

                logger.info(
                    f"[更新] 内容更新完成 (old_id={memory_id} → new_id={new_memory_id})"
                )
                self._invalidate_search_cache()
                return True

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"[更新] 内容更新失败 (memory_id={memory_id}): {e}", exc_info=True
                )
                return False

        # 处理非内容的元数据更新（不需要重建索引）
        metadata_updates = {}

        if "importance" in updates:
            metadata_updates["importance"] = clamp_float(
                updates["importance"], default=0.5
            )

        if "metadata" in updates:
            metadata_updates.update(updates["metadata"])

        if metadata_updates:
            # 确保 current_metadata 是字典（再次检查）
            current_metadata = safe_json_dict(current_metadata)

            # 合并元数据
            current_metadata.update(metadata_updates)
            current_metadata["updated_at"] = time.time()

            # 使用写侧索引维护器确保多存储元数据同步
            if self.index_writer is None:
                logger.error("索引写入器未初始化")
                return False
            success = await self.index_writer.update_metadata(
                memory_id, metadata_updates
            )

            if success:
                logger.info(f"[更新] 元数据更新成功 (memory_id={memory_id})")
                # 仅当更新触及图谱提取依赖的字段时才重建图谱索引；
                # 状态/类型等纯元数据编辑重建整图（删节点+FAISS 落盘）纯属浪费
                graph_affecting = metadata_updates.keys() & {
                    "participants",
                    "participant_identities",
                    "topics",
                    "key_facts",
                    "canonical_summary",
                    "session_id",
                    "persona_id",
                    "importance",
                    "create_time",
                    "summary_schema_version",
                    "source_window",
                }
                if graph_affecting and self.graph_memory_manager is not None:
                    await self.graph_memory_manager.index_memory(
                        memory_id,
                        memory["text"],
                        current_metadata,
                    )
                self._invalidate_search_cache()
            else:
                logger.error(f"[更新] 元数据更新失败 (memory_id={memory_id})")

            return success

        return True

    async def replace_memory(
        self,
        memory_id: int,
        *,
        content: str,
        metadata: dict[str, Any],
        importance: float,
    ) -> int:
        """Replace one memory and rebuild all derived data with a new ID."""
        current = await self.get_memory(memory_id)
        if not current:
            raise ValueError(f"记忆不存在 (memory_id={memory_id})")
        if not content or not content.strip():
            raise ValueError("记忆内容不能为空")

        current_metadata = safe_json_dict(current.get("metadata"))
        source_messages = await self.get_memory_source(memory_id)
        replacement_metadata = current_metadata.copy()
        replacement_metadata.update(metadata or {})
        replacement_metadata["previous_id"] = memory_id
        replacement_metadata["updated_at"] = time.time()
        replacement_metadata["create_time"] = current_metadata.get("create_time")
        normalized_importance = clamp_float(importance, default=0.5)
        replacement_metadata["importance"] = normalized_importance

        session_id = replacement_metadata.get("session_id")
        persona_id = replacement_metadata.get("persona_id")
        raw_key_facts = replacement_metadata.get("key_facts")
        raw_topics = replacement_metadata.get("topics")
        raw_participants = replacement_metadata.get("participants")
        key_facts = raw_key_facts if isinstance(raw_key_facts, list) else []
        topics = raw_topics if isinstance(raw_topics, list) else []
        participants = raw_participants if isinstance(raw_participants, list) else []
        atoms = []
        if self.atom_enabled:
            atoms = classify_atoms(
                key_facts=key_facts,
                topics=topics,
                participants=participants,
                parent_importance=normalized_importance,
                session_id=session_id,
                persona_id=persona_id,
            )

        new_memory_id: int | None = None
        add_task = self._create_tracked_task(
            self.add_memory(
                content=content,
                session_id=session_id,
                persona_id=persona_id,
                importance=normalized_importance,
                metadata=replacement_metadata,
                atoms=atoms,
                preserve_create_time=True,
                source_messages=source_messages or None,
            )
        )
        try:
            new_memory_id = await asyncio.shield(add_task)
            if new_memory_id is None:
                raise RuntimeError("新记忆创建失败")
            if not await self.delete_memory(memory_id):
                await self.delete_memory(new_memory_id)
                new_memory_id = None
                raise RuntimeError("旧记忆删除失败，已回滚新记忆")
            self._invalidate_search_cache()
            return new_memory_id
        except asyncio.CancelledError:
            if new_memory_id is None:
                try:
                    new_memory_id = await asyncio.shield(add_task)
                except Exception:
                    new_memory_id = None
            if new_memory_id is not None:
                await asyncio.shield(self.delete_memory(new_memory_id))
            self._invalidate_search_cache()
            raise
        except Exception:
            if new_memory_id is not None:
                try:
                    if await self.get_memory(new_memory_id):
                        await self.delete_memory(new_memory_id)
                except Exception:
                    logger.error(
                        f"[更新] 回滚新记忆失败 (memory_id={new_memory_id})",
                        exc_info=True,
                    )
            self._invalidate_search_cache()
            raise

    async def delete_memory(self, memory_id: int) -> bool:
        """
        删除记忆

        Args:
            memory_id: 记忆ID

        Returns:
            bool: 是否删除成功
        """

        if (
            current_adapter_access.get() is not None
            and await self.get_memory(memory_id) is None
        ):
            return False

        # index_writer.delete_memory() 内部已按顺序删除 BM25、向量索引和 documents 表
        if self.index_writer is None:
            logger.error("索引写入器未初始化")
            return False
        success = await self.index_writer.delete_memory(memory_id)
        if not success:
            return False

        if self.db_connection is not None:
            async with self._write_transaction() as connection:
                await connection.execute(
                    "DELETE FROM memory_sources WHERE memory_id = ?", (memory_id,)
                )

        try:
            if self.graph_memory_manager is not None:
                deleted = await self.graph_memory_manager.delete_memory(memory_id)
                if deleted is False:
                    raise RuntimeError(
                        "Graph deletion deferred until rebuild completes"
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[MemoryEngine] 图记忆删除失败 (memory_id={memory_id})",
                exc_info=True,
            )

        try:
            if self.atom_store is not None:
                await self.atom_store.delete_by_parent(memory_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[MemoryEngine] 记忆原子删除失败 (memory_id={memory_id})",
                exc_info=True,
            )

        self._invalidate_search_cache()
        return success

    async def merge_duplicate_memory(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        importance: float,
        metadata: dict[str, Any] | None = None,
        atoms: list | None = None,
        source_messages: list[dict[str, Any]] | None = None,
    ) -> int | None:
        """Merge a new summary into an existing near-duplicate memory.

        The merged body keeps the more complete variant (or the de-duplicated
        concatenation of both). When the body changes it is rebuilt through
        :meth:`replace_memory`, so the embedding, BM25/FTS index, graph entries
        and atoms all describe the merged text instead of the stale old one.
        Metadata-only merges keep the current document id and sync the affected
        indexes in place.

        Args:
            memory_id: Existing memory document id.
            content: Incoming summary text; ``None`` keeps the existing body.
            importance: Importance of the incoming summary.
            metadata: Incoming summary metadata.
            atoms: Newly classified memory atoms.
            source_messages: Optional retained source messages.

        Returns:
            The surviving memory id (``memory_id`` or its replacement), or
            ``None`` when the merge could not be persisted.

        Raises:
            asyncio.CancelledError: Re-raised so rollback compensation still runs.
        """
        if self.db_connection is None:
            return None
        memory = await self.get_memory(memory_id)
        if not memory:
            return None
        metadata = metadata or {}
        current_metadata = safe_json_dict(memory.get("metadata"))
        current_content = str(memory.get("text") or "")
        merged_content = self._merge_memory_text(current_content, content or "")
        patch: dict[str, Any] = {}

        for key in ("topics", "key_facts", "participants", "participant_identities"):
            incoming = metadata.get(key)
            if not incoming:
                continue
            merged = self._merge_unique_values(current_metadata.get(key), incoming)
            if merged:
                patch[key] = merged

        now = time.time()
        source_window = metadata.get("source_window")
        if isinstance(source_window, dict):
            windows = list(current_metadata.get("source_windows") or [])
            windows.append(source_window)
            patch["source_windows"] = windows[-20:]

        patch["last_access_time"] = now
        patch["dedup_merge_count"] = (
            int(current_metadata.get("dedup_merge_count") or 0) + 1
        )
        patch["dedup_last_merged_at"] = now

        current_importance = clamp_float(
            current_metadata.get("importance"), default=0.5
        )
        merged_importance = max(
            current_importance, clamp_float(importance, default=0.5)
        )

        content_changed = merged_content.strip() != current_content.strip()
        if content_changed:
            patch["canonical_summary"] = merged_content
        try:
            if content_changed:
                # 内容变化必须重建向量/FTS/图/原子；replace_memory 先写新记忆再删旧，
                # 任何一步失败都会回滚，不会留下半同步状态。
                target_id = await self.replace_memory(
                    memory_id,
                    content=merged_content,
                    metadata=patch,
                    importance=merged_importance,
                )
            else:
                if not await self.update_memory(
                    memory_id,
                    {"importance": merged_importance, "metadata": patch},
                ):
                    return None
                target_id = memory_id
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                f"[MemoryEngine] 合并近重复记忆后重建索引失败 (memory_id={memory_id})",
                exc_info=True,
            )
            await self._mark_index_rebuild_needed(
                "dedup_merge_rebuild_failed", memory_id
            )
            return None

        if atoms and self.atom_store is not None and self.atom_enabled:
            await self._append_atoms_to_memory(target_id, atoms, metadata)

        dedup_cfg = self.config.get("memory_dedup") or {}
        if source_messages and bool(dedup_cfg.get("merge_sources", True)):
            max_sources = int(dedup_cfg.get("max_source_messages", 50) or 50)
            existing_sources = await self.get_memory_source(target_id)
            combined: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in [*existing_sources, *source_messages]:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                combined.append(item)
            try:
                await self.save_memory_source(target_id, combined[-max_sources:])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    f"[MemoryEngine] 合并来源消息失败 (memory_id={target_id})",
                    exc_info=True,
                )

        logger.info(
            f"[MemoryEngine] 已合并近重复记忆到 #{target_id}"
            f"（相似度合并次数={patch['dedup_merge_count']}，"
            f"正文{'已重建' if content_changed else '未变'}）"
        )
        return target_id

    @staticmethod
    def _merge_memory_text(existing: str, incoming: str) -> str:
        """Return the merged summary body for a dedup merge.

        Keeps the more complete variant when one body already contains the
        other, and otherwise concatenates the non-overlapping sentences so facts
        introduced by the newer summary stay retrievable. When the union exceeds
        ``_MERGED_CONTENT_MAX_CHARS`` the longer single body wins, which bounds
        the cost of re-embedding repeated merges.

        Args:
            existing: Body stored on the existing memory.
            incoming: Body of the incoming summary.

        Returns:
            Body to persist on the surviving memory.
        """
        left = str(existing or "").strip()
        right = str(incoming or "").strip()
        if not left:
            return right
        if not right:
            return left
        if left == right or right in left:
            return left
        if left in right:
            return right

        seen: set[str] = set()
        merged = ""
        for text in (left, right):
            for segment in _SENTENCE_BOUNDARY.split(text):
                piece = segment.strip()
                if not piece or piece in seen:
                    continue
                seen.add(piece)
                if not merged:
                    merged = piece
                elif merged[-1].isascii() and merged[-1] not in "。！？；\n":
                    merged = f"{merged} {piece}"
                else:
                    merged += piece
        if len(merged) > _MERGED_CONTENT_MAX_CHARS:
            return left if len(left) >= len(right) else right
        return merged or (left if len(left) >= len(right) else right)

    async def _mark_index_rebuild_needed(self, reason: str, memory_id: int) -> None:
        """Flag startup index maintenance after an unsafe merge rebuild.

        Args:
            reason: Short machine-readable reason recorded in the log.
            memory_id: Memory whose derived indexes may be stale.
        """
        logger.error(
            f"[MemoryEngine] {reason}: 检索索引可能未同步，"
            f"已标记 needs_index_rebuild 以便启动时重建 (memory_id={memory_id})"
        )
        writer = getattr(self, "_write_transaction", None)
        if self.db_connection is None or not callable(writer):
            return
        try:
            async with writer() as connection:
                await connection.execute(
                    "INSERT OR REPLACE INTO migration_status (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    ("needs_index_rebuild", "true"),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "[MemoryEngine] 写入 needs_index_rebuild 标记失败", exc_info=True
            )

    @staticmethod
    def _merge_unique_values(existing: Any, incoming: Any) -> list[Any]:
        """Union two metadata lists without reordering existing entries."""
        merged: list[Any] = []
        seen: set[str] = set()
        for item in [*(existing or []), *(incoming or [])]:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    async def _append_atoms_to_memory(
        self,
        memory_id: int,
        atoms: list,
        metadata: dict[str, Any],
    ) -> None:
        """Attach newly classified atoms to an existing memory, skipping duplicates."""
        if self.db_connection is None or self.atom_store is None:
            return
        cursor = await self.db_connection.execute(
            "SELECT content FROM memory_atoms WHERE parent_memory_id = ?",
            (int(memory_id),),
        )
        existing_contents = {
            str(row[0]).strip() for row in await cursor.fetchall() if row[0]
        }
        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")
        for atom in atoms:
            content = str(getattr(atom, "content", "") or "").strip()
            if not content or content in existing_contents:
                continue
            atom.metadata["adapter_id"] = metadata.get("adapter_id")
            atom.parent_memory_id = memory_id
            atom.session_id = atom.session_id or session_id
            atom.persona_id = atom.persona_id or persona_id
            try:
                await self.atom_store.insert(atom)
                existing_contents.add(content)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    f"[MemoryEngine] 合并记忆原子失败 (memory_id={memory_id}): {content[:60]}",
                    exc_info=True,
                )

    async def _delete_graph_and_atoms_for_batch(self, memory_ids: list[int]) -> None:
        if not memory_ids:
            return
        if self.graph_memory_manager is not None:
            deleted = await self.graph_memory_manager.batch_delete_memories(memory_ids)
            if deleted is False:
                raise RuntimeError("Graph deletion deferred until rebuild completes")
        if self.atom_store is not None:
            await self.atom_store.batch_delete_by_parent(memory_ids)

    async def _drop_legacy_documents_fts_triggers(
        self, connection: aiosqlite.Connection
    ):
        cursor = await connection.execute("""
            SELECT name FROM sqlite_master
            WHERE type='trigger' AND tbl_name='documents'
              AND sql LIKE '%documents_fts%'
        """)
        rows = await cursor.fetchall()
        for row in rows:
            trigger_name = row[0]
            await connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
            logger.warning(f"已清理旧 LivingMemory FTS 触发器: {trigger_name}")

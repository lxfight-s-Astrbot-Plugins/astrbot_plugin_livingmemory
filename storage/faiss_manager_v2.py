# -*- coding: utf-8 -*-
import uuid
import json
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
import aiosqlite

from astrbot.api import logger

from ..core.models.memory_models import (
    Memory,
    AccessInfo,  # noqa: F401
    UserFeedback,  # noqa: F401
    EmotionalValence,  # noqa: F401
)
from .memory_storage import MemoryStorage
from .vector_store import VectorStore
from .graph_storage import GraphStorageSQLite


class FaissManagerV2:
    """
    高级管理器，协调 MemoryStorage 和 VectorStore，
    以支持结构化的、具有生命周期的记忆。
    """

    def __init__(
        self,
        db_path: str,
        text_vstore: VectorStore,
        image_vstore: VectorStore,
        embedding_model,
    ):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        self.storage: Optional[MemoryStorage] = None
        self.graph_storage: Optional[GraphStorageSQLite] = None

        self.text_vstore = text_vstore
        self.image_vstore = image_vstore
        self.embedding_model = embedding_model

    async def initialize(self):
        """
        建立数据库连接，并初始化所有依赖于此连接的存储组件。
        """
        self.conn = await aiosqlite.connect(self.db_path)
        # 启用外键约束，这对于 ON DELETE CASCADE 至关重要
        await self.conn.execute("PRAGMA foreign_keys = ON;")

        # 初始化各个存储层
        self.storage = MemoryStorage(self.conn)  # 传入连接对象
        await self.storage.initialize_schema()  # 创建 memories 表

        self.graph_storage = GraphStorageSQLite(self.conn)  # 传入同一个连接对象
        await self.graph_storage.initialize_schema()  # 创建图相关的表

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def add_memory(self, memory: Memory) -> str:
        """
        添加一条新的、结构化的记忆。
        """
        if not memory.memory_id:
            memory.memory_id = str(uuid.uuid4())

        if not memory.embedding:
            memory.embedding = await asyncio.to_thread(
                self.embedding_model.encode, memory.description
            )

        # 1. 存入 SQLite 并获取内部 ID
        internal_id = await self.storage.add_memory(memory)

        # 2. 将文本向量添加到 text_vstore
        if memory.embedding:
            await asyncio.to_thread(
                self.text_vstore.add, [internal_id], [memory.embedding]
            )

        # 3. 将图像向量添加到 image_vstore
        media_embeddings = [
            media.embedding for media in memory.linked_media if media.embedding
        ]
        if media_embeddings:
            media_ids = [internal_id] * len(media_embeddings)
            await asyncio.to_thread(self.image_vstore.add, media_ids, media_embeddings)

        # 4. 将图数据添加到 graph_storage
        if memory.knowledge_graph_payload:
            await self.graph_storage.add_memory_graph(
                internal_id, memory.knowledge_graph_payload.to_dict()
            )

        # TODO 考虑定期保存索引，而不是每次都保存
        await asyncio.to_thread(self.text_vstore.save_index)
        await asyncio.to_thread(self.image_vstore.save_index)

        return memory.memory_id

    async def search_memory(
        self, query_text: str, k: int = 10, w1: float = 0.6, w2: float = 0.4
    ) -> List[Memory]:
        """
        根据查询文本智能检索最相关的记忆。
        """
        # 1a. 向量搜索
        query_embedding = await asyncio.to_thread(
            self.embedding_model.encode, query_text
        )
        # 召回数量可以设置得比最终需要的 k 要大，例如 k*5
        distances, text_ids = await asyncio.to_thread(
            self.text_vstore.search, query_embedding, k * 5
        )

        # 1b. 基于图的种子扩展
        # 假设 embedding_model 有提取实体的能力
        query_entities = await asyncio.to_thread(
            self.embedding_model.extract_entities, query_text
        )
        graph_ids = []
        if query_entities:
            for entity_id in query_entities:
                graph_ids.extend(
                    await self.graph_storage.find_related_memory_ids(entity_id)
                )

        # 合并候选并去重
        candidate_internal_ids = list(set(list(text_ids) + graph_ids))
        if not candidate_internal_ids:
            return []

        # --- 阶段二：重排与扩展 ---

        # 2a. 获取候选记忆的完整数据
        candidate_docs = await self.storage.get_memories_by_internal_ids(
            candidate_internal_ids
        )
        candidate_memories = {
            doc["id"]: Memory.from_dict(json.loads(doc["memory_data"]))
            for doc in candidate_docs
        }

        # 2b. 计算最终分数并重排
        final_scores = {}
        text_ids_list = list(text_ids)
        for internal_id in candidate_internal_ids:
            # Faiss 分数：可以用排名的倒数来表示，排名越靠前分数越高
            try:
                faiss_score = 1.0 / (text_ids_list.index(internal_id) + 1)
            except ValueError:
                faiss_score = 0.0

            # 图分数：如果记忆在图扩展的结果中，则获得加分
            graph_score = 1.0 if internal_id in graph_ids else 0.0

            final_scores[internal_id] = (w1 * faiss_score) + (w2 * graph_score)

        # 根据最终分数降序排序
        sorted_ids = sorted(
            final_scores.keys(), key=lambda id: final_scores[id], reverse=True
        )

        # 获取Top-K结果，并更新访问信息
        top_k_results = [
            candidate_memories[id] for id in sorted_ids[:k] if id in candidate_memories
        ]
        await self.update_memory_access_info([mem.memory_id for mem in top_k_results])

        return top_k_results

    async def update_memory_access_info(self, memory_ids: List[str]):
        """
        批量更新一组记忆的最后访问时间和访问计数。
        """
        docs = await self.storage.get_memories_by_memory_ids(memory_ids)
        if not docs:
            return

        updates = []
        for doc in docs:
            try:
                memory_dict = json.loads(doc["memory_data"])
                access_info = memory_dict["metadata"]["access_info"]
                access_info["last_accessed_timestamp"] = datetime.now(
                    timezone.utc
                ).isoformat()
                access_info["access_count"] = access_info.get("access_count", 0) + 1

                updates.append(
                    {
                        "memory_id": memory_dict["memory_id"],
                        "memory_data": json.dumps(memory_dict),
                    }
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(
                    f"Error updating access info for memory_id {doc.get('memory_id')}: {e}"
                )

        if updates:
            await self.storage.update_memories(updates)

    async def get_all_memories_for_forgetting(self) -> List[Memory]:
        """获取所有记忆，用于遗忘代理的处理。"""
        all_docs = await self.storage.get_all_memories()
        return [Memory.from_dict(json.loads(doc["memory_data"])) for doc in all_docs]

    async def update_memories_metadata(self, memories: List[Memory]):
        """批量更新记忆的完整对象。"""
        if not memories:
            return
        updates = [
            {"memory_id": mem.memory_id, "memory_data": json.dumps(mem.to_dict())}
            for mem in memories
        ]
        await self.storage.update_memories(updates)

    async def delete_memories(self, memory_ids: List[str]):
        """
        批量删除记忆。由于设置了外键级联删除，图关系会被自动清理。
        """
        docs = await self.storage.get_memories_by_memory_ids(memory_ids)
        if not docs:
            return

        internal_ids = [doc["id"] for doc in docs]

        # 1. 从 Faiss 移除
        await asyncio.to_thread(self.text_vstore.remove, internal_ids)
        await asyncio.to_thread(self.image_vstore.remove, internal_ids)
        await asyncio.to_thread(self.text_vstore.save_index)
        await asyncio.to_thread(self.image_vstore.save_index)

        # 2. 从 SQLite 的 memories 表移除
        # 由于设置了 ON DELETE CASCADE，graph_edges 表中相关的数据会被自动删除
        await self.storage.delete_memories_by_internal_ids(internal_ids)

    async def archive_memory(self, memory_id: str):
        """实现遗忘逻辑的第一步：归档。"""
        # 1. 获取记忆的 internal_id
        docs = await self.storage.get_memories_by_memory_ids([memory_id])
        if not docs:
            return
        internal_id = docs[0]["id"]

        # 2. 从所有 Faiss 索引中移除
        await asyncio.to_thread(self.text_vstore.remove, [internal_id])
        await asyncio.to_thread(self.image_vstore.remove, [internal_id])
        # (可选) 也可以从图数据库中移除以节省空间
        # await self.graph_storage.delete_graph_for_memory(internal_id)

        # 3. 在 SQLite 中更新状态
        await self.storage.update_memory_status([internal_id], "archived")

        logger.info(f"已归档记忆 {memory_id}。")

# -*- coding: utf-8 -*-
"""
统一记忆引擎 - MemoryEngine
提供统一的记忆管理接口,整合所有底层组件
"""

import asyncio
import time
import aiosqlite
import json
from typing import List, Dict, Any, Optional
from pathlib import Path

from .retrieval.hybrid_retriever import HybridRetriever, HybridResult
from .retrieval.bm25_retriever import BM25Retriever
from .retrieval.vector_retriever import VectorRetriever
from .retrieval.rrf_fusion import RRFFusion
from .text_processor import TextProcessor


class MemoryEngine:
    """
    统一记忆引擎

    整合BM25检索、向量检索和混合检索,提供完整的记忆管理接口。

    主要功能:
    1. 记忆CRUD操作(添加、检索、更新、删除)
    2. 自动化记忆整理和清理
    3. 重要性评估和时间衰减
    4. 会话隔离和统计
    """

    def __init__(
        self,
        db_path: str,
        faiss_db,
        llm_provider=None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化记忆引擎

        Args:
            db_path: SQLite数据库路径
            faiss_db: FAISS向量数据库实例
            llm_provider: LLM提供者(可选,用于高级功能)
            config: 配置字典,支持以下参数:
                - rrf_k: RRF参数,默认60
                - decay_rate: 时间衰减率,默认0.01
                - importance_weight: 重要性权重,默认1.0
                - fallback_enabled: 启用退化机制,默认True
                - cleanup_days_threshold: 清理天数阈值,默认30
                - cleanup_importance_threshold: 清理重要性阈值,默认0.3
                - stopwords_path: 停用词文件路径(可选)
        """
        self.db_path = db_path
        self.faiss_db = faiss_db
        self.llm_provider = llm_provider
        self.config = config or {}

        # 确保数据库目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 初始化组件(在initialize中完成)
        self.text_processor = None
        self.bm25_retriever = None
        self.vector_retriever = None
        self.rrf_fusion = None
        self.hybrid_retriever = None
        self.db_connection = None

    async def initialize(self):
        """
        异步初始化引擎

        创建数据库表、初始化所有检索器组件
        """
        # 1. 连接数据库
        self.db_connection = await aiosqlite.connect(self.db_path)
        self.db_connection.row_factory = aiosqlite.Row

        # 2. 创建表结构
        await self._create_tables()

        # 3. 初始化文本处理器
        stopwords_path = self.config.get("stopwords_path")
        self.text_processor = TextProcessor(stopwords_path)

        # 4. 初始化RRF融合器
        rrf_k = self.config.get("rrf_k", 60)
        self.rrf_fusion = RRFFusion(k=rrf_k)

        # 5. 初始化BM25检索器
        self.bm25_retriever = BM25Retriever(
            self.db_path, self.text_processor, self.config
        )
        await self.bm25_retriever.initialize()

        # 6. 初始化向量检索器
        self.vector_retriever = VectorRetriever(
            self.faiss_db, self.text_processor, self.config
        )

        # 7. 初始化混合检索器
        self.hybrid_retriever = HybridRetriever(
            self.bm25_retriever, self.vector_retriever, self.rrf_fusion, self.config
        )

    async def close(self):
        """关闭数据库连接和清理资源"""
        if self.db_connection:
            await self.db_connection.close()

    async def _create_tables(self):
        """创建数据库表"""
        # documents表 - 存储文档内容和元数据
        await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
        """)

        # 创建索引
        await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_metadata
            ON documents(json_extract(metadata, '$.session_id'))
        """)

        await self.db_connection.commit()

    # ==================== 核心记忆操作 ====================

    async def add_memory(
        self,
        content: str,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        添加新记忆

        Args:
            content: 记忆内容
            session_id: 会话ID
            persona_id: 人格ID
            importance: 重要性(0-1)
            metadata: 额外元数据

        Returns:
            int: 记忆ID(doc_id)
        """
        if not content or not content.strip():
            raise ValueError("记忆内容不能为空")

        # 准备完整元数据
        current_time = time.time()
        full_metadata = {
            "session_id": session_id,
            "persona_id": persona_id,
            "importance": max(0.0, min(1.0, importance)),  # 限制在0-1范围
            "create_time": current_time,
            "last_access_time": current_time,
        }

        # 合并用户提供的额外元数据
        if metadata:
            full_metadata.update(metadata)

        # 通过混合检索器添加(会同时添加到BM25和向量索引)
        doc_id = await self.hybrid_retriever.add_memory(content, full_metadata)

        return doc_id

    async def search_memories(
        self,
        query: str,
        k: int = 5,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[HybridResult]:
        """
        检索相关记忆

        Args:
            query: 查询字符串
            k: 返回数量
            session_id: 会话ID过滤(可选)
            persona_id: 人格ID过滤(可选)

        Returns:
            List[HybridResult]: 检索结果列表
        """
        if not query or not query.strip():
            return []

        # 执行混合检索
        results = await self.hybrid_retriever.search(query, k, session_id, persona_id)

        # 异步更新访问时间(不阻塞返回)
        for result in results:
            asyncio.create_task(self._update_access_time_internal(result.doc_id))

        return results

    async def get_memory(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取记忆

        Args:
            memory_id: 记忆ID

        Returns:
            Optional[Dict]: 记忆数据,包含text和metadata
        """
        # 从faiss_db的document_storage获取文档
        try:
            doc = await self.faiss_db.document_storage.get_document(memory_id)
            if not doc:
                return None

            return {
                "id": doc["id"],
                "text": doc["text"],
                "metadata": doc["metadata"],
            }
        except Exception:
            return None

    async def update_memory(
        self,
        memory_id: int,
        updates: Dict[str, Any],
    ) -> bool:
        """
        更新记忆

        支持更新内容、重要性、元数据等

        Args:
            memory_id: 记忆ID
            updates: 更新字典,可包含:
                - content: 新内容
                - importance: 新重要性
                - metadata: 元数据更新

        Returns:
            bool: 是否更新成功
        """
        # 获取当前记忆
        memory = await self.get_memory(memory_id)
        if not memory:
            return False

        current_metadata = memory["metadata"]

        # 处理内容更新
        if "content" in updates:
            new_content = updates["content"]
            if not new_content or not new_content.strip():
                return False

            # 更新documents表
            await self.db_connection.execute(
                "UPDATE documents SET text = ? WHERE id = ?", (new_content, memory_id)
            )
            await self.db_connection.commit()

            # TODO: 需要重新生成向量和更新BM25索引
            # 这里暂时只更新数据库,完整实现需要重建索引

        # 处理元数据更新
        metadata_updates = {}

        if "importance" in updates:
            metadata_updates["importance"] = max(0.0, min(1.0, updates["importance"]))

        if "metadata" in updates:
            metadata_updates.update(updates["metadata"])

        if metadata_updates:
            # 合并元数据
            current_metadata.update(metadata_updates)

            # 更新到混合检索器
            success = await self.hybrid_retriever.update_metadata(
                memory_id, metadata_updates
            )

            return success

        return True

    async def delete_memory(self, memory_id: int) -> bool:
        """
        删除记忆

        Args:
            memory_id: 记忆ID

        Returns:
            bool: 是否删除成功
        """
        # 通过混合检索器删除(会同时删除BM25和向量索引)
        success = await self.hybrid_retriever.delete_memory(memory_id)

        return success

    # ==================== 高级功能 ====================

    async def update_importance(self, memory_id: int, new_importance: float) -> bool:
        """
        更新记忆重要性

        Args:
            memory_id: 记忆ID
            new_importance: 新重要性值(0-1)

        Returns:
            bool: 是否更新成功
        """
        return await self.update_memory(memory_id, {"importance": new_importance})

    async def update_access_time(self, memory_id: int) -> bool:
        """
        更新最后访问时间

        Args:
            memory_id: 记忆ID

        Returns:
            bool: 是否更新成功
        """
        return await self._update_access_time_internal(memory_id)

    async def _update_access_time_internal(self, memory_id: int) -> bool:
        """内部方法:更新访问时间"""
        current_time = time.time()
        metadata_update = {"last_access_time": current_time}

        return await self.hybrid_retriever.update_metadata(memory_id, metadata_update)

    async def get_session_memories(
        self,
        session_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        获取会话的所有记忆

        Args:
            session_id: 会话ID
            limit: 限制数量

        Returns:
            List[Dict]: 记忆列表
        """
        # 从faiss_db的document_storage获取所有文档并过滤
        try:
            all_docs = await self.faiss_db.document_storage.get_documents(
                metadata_filters={"session_id": session_id}
            )
            
            # 按创建时间排序
            sorted_docs = sorted(
                all_docs,
                key=lambda x: x["metadata"].get("create_time", 0),
                reverse=True
            )
            
            # 限制数量
            limited_docs = sorted_docs[:limit]
            
            memories = []
            for doc in limited_docs:
                memories.append({
                    "id": doc["id"],
                    "text": doc["text"],
                    "metadata": doc["metadata"],
                })
            
            return memories
        except Exception:
            return []

    async def cleanup_old_memories(
        self,
        days_threshold: Optional[int] = None,
        importance_threshold: Optional[float] = None,
    ) -> int:
        """
        清理旧记忆

        删除超过阈值且重要性低的记忆

        Args:
            days_threshold: 天数阈值,默认从配置读取
            importance_threshold: 重要性阈值,默认从配置读取

        Returns:
            int: 删除的记忆数量
        """
        # 使用配置或参数值
        days = days_threshold or self.config.get("cleanup_days_threshold", 30)
        importance = importance_threshold or self.config.get(
            "cleanup_importance_threshold", 0.3
        )

        cutoff_time = time.time() - (days * 86400)

        # 从faiss_db获取所有文档并过滤
        try:
            all_docs = await self.faiss_db.document_storage.get_documents(
                metadata_filters={}
            )
            
            # 找到符合清理条件的记忆
            to_delete = []
            for doc in all_docs:
                metadata = doc["metadata"]
                create_time = metadata.get("create_time", time.time())
                doc_importance = metadata.get("importance", 0.5)
                
                if create_time < cutoff_time and doc_importance < importance:
                    to_delete.append(doc["id"])
            
            # 批量删除
            deleted_count = 0
            for memory_id in to_delete:
                success = await self.delete_memory(memory_id)
                if success:
                    deleted_count += 1
            
            return deleted_count
        except Exception:
            return 0

    async def get_statistics(self) -> Dict[str, Any]:
        """
        获取记忆统计信息

        Returns:
            Dict: 统计信息,包含:
                - total_memories: 总记忆数
                - sessions: 各会话的记忆数
                - avg_importance: 平均重要性
                - oldest_memory: 最旧记忆时间
                - newest_memory: 最新记忆时间
        """
        try:
            # 从faiss_db获取所有文档
            all_docs = await self.faiss_db.document_storage.get_documents(
                metadata_filters={}
            )
            
            stats = {}
            
            # 总记忆数
            stats["total_memories"] = len(all_docs)
            
            # 各会话记忆数
            session_counts = {}
            importance_sum = 0
            importance_count = 0
            oldest_time = None
            newest_time = None
            
            for doc in all_docs:
                metadata = doc["metadata"]
                
                # 统计会话
                session_id = metadata.get("session_id")
                if session_id:
                    session_counts[session_id] = session_counts.get(session_id, 0) + 1
                
                # 统计重要性
                importance = metadata.get("importance")
                if importance is not None:
                    importance_sum += importance
                    importance_count += 1
                
                # 统计时间
                create_time = metadata.get("create_time")
                if create_time:
                    if oldest_time is None or create_time < oldest_time:
                        oldest_time = create_time
                    if newest_time is None or create_time > newest_time:
                        newest_time = create_time
            
            stats["sessions"] = session_counts
            stats["avg_importance"] = (
                importance_sum / importance_count if importance_count > 0 else 0.0
            )
            stats["oldest_memory"] = oldest_time
            stats["newest_memory"] = newest_time
            
            return stats
        except Exception:
            return {
                "total_memories": 0,
                "sessions": {},
                "avg_importance": 0.0,
                "oldest_memory": None,
                "newest_memory": None,
            }

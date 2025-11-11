"""
统一记忆引擎 - MemoryEngine
提供统一的记忆管理接口,整合所有底层组件
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from astrbot.api import logger
from .retrieval.bm25_retriever import BM25Retriever
from .retrieval.hybrid_retriever import HybridResult, HybridRetriever
from .retrieval.rrf_fusion import RRFFusion
from .retrieval.vector_retriever import VectorRetriever
from .text_processor import TextProcessor


def _extract_session_uuid(session_id: str | None) -> str | None:
    """
    从 session_id 中提取 UUID 部分用于比较

    支持两种格式：
    - 新版本：platform:message_type:uuid → 返回 uuid
    - 旧版本：uuid → 返回 uuid

    Args:
        session_id: session_id 字符串

    Returns:
        Optional[str]: 提取出的 UUID 部分，如果无法提取则返回原值
    """
    if not session_id:
        return None

    # 尝试按新版本格式分割（冒号或感叹号分隔）
    if ":" in session_id:
        parts = session_id.split(":")
        return parts[-1]  # 返回最后一部分（UUID）
    elif "!" in session_id:
        parts = session_id.split("!")
        return parts[-1]  # 返回最后一部分（UUID）

    # 已经是 UUID 格式，直接返回
    return session_id


class MemoryEngine:
    """
    统一记忆引擎

    整合BM25检索、向量检索和混合检索,提供完整的记忆管理接口。

    主要功能:
    1. 记忆CRUD操作(添加、检索、更新、删除)
    2. 自动化记忆整理和清理
    3. 重要性评估和时间衰减
    4. 会话隔离和统计

    ID管理体系说明：
    ==================
    本系统使用三层存储架构，统一使用整数ID作为主键：

    1. **DocumentStorage (FAISS内部)**
       - 表: documents (SQLite，由SQLAlchemy管理)
       - 主键: id (INTEGER, AUTOINCREMENT) - 这是统一的整数标识符
       - UUID字段: doc_id (TEXT) - FAISS内部使用的UUID字符串
       - 关系: id ←→ doc_id (一对一映射)

    2. **BM25 FTS5索引**
       - 表: memories_fts (SQLite FTS5虚拟表)
       - 字段: doc_id (UNINDEXED) - 引用documents.id的整数
       - 注意: 只存储分词后的内容，metadata从documents表读取

    3. **FAISS向量索引**
       - 存储: EmbeddingStorage (FAISS索引文件)
       - 索引ID: 使用documents.id作为向量的整数索引

    插件对外接口：
    - add_memory() 返回: int (documents.id)
    - search_memories() 返回: HybridResult包含doc_id (int)
    - update_memory(memory_id: int) 参数: documents.id
    - delete_memory(memory_id: int) 参数: documents.id

    同步保证：
    - 添加: 先插入DocumentStorage获取id，再用此id插入BM25和FAISS
    - 更新: 通过vector_retriever更新DocumentStorage (自动同步)
    - 删除: 先删除BM25，再通过FaissVecDB.delete()删除DocumentStorage和向量
    """

    def __init__(
        self,
        db_path: str,
        faiss_db,
        llm_provider=None,
        config: dict[str, Any] | None = None,
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
        """创建数据库表

        注意：documents 表主要由 FAISS 的 DocumentStorage 类创建和管理。
        这里使用 CREATE TABLE IF NOT EXISTS 确保兼容性：
        - 如果 FAISS 已创建，不会重复创建（IF NOT EXISTS）
        - 如果 FAISS 未创建（极端情况），插件仍能正常工作
        - 插件需要直接操作此表进行高频更新（如访问时间）
        """
        # documents表 - 与FAISS共享，IF NOT EXISTS确保不重复创建
        await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
        """)

        # 创建索引以提升session_id查询性能
        await self.db_connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_doc_metadata
            ON documents(json_extract(metadata, '$.session_id'))
        """)

        # 创建版本管理表
        await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS db_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                migrated_at TEXT NOT NULL,
                migration_duration_seconds REAL
            )
        """)

        # 创建迁移状态表
        await self.db_connection.execute("""
            CREATE TABLE IF NOT EXISTS migration_status (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)

        await self.db_connection.commit()

        # 检查是否需要初始化版本信息
        cursor = await self.db_connection.execute("SELECT COUNT(*) FROM db_version")
        version_count = (await cursor.fetchone())[0]

        if version_count == 0:
            # 全新数据库，设置初始版本为 2
            from datetime import datetime

            await self.db_connection.execute(
                """
                INSERT INTO db_version (version, description, migrated_at, migration_duration_seconds)
                VALUES (?, ?, ?, ?)
            """,
                (2, "初始版本 - v2架构", datetime.utcnow().isoformat(), 0.0),
            )
            await self.db_connection.commit()

            from astrbot.api import logger

            logger.info("已初始化数据库版本信息: v2")

    # ==================== 核心记忆操作 ====================

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """
        添加新记忆

        Args:
            content: 记忆内容
            session_id: 会话ID(支持多种格式,自动提取UUID)
            persona_id: 人格ID(支持多种格式,自动提取UUID)
            importance: 重要性(0-1)
            metadata: 额外元数据

        Returns:
            int: 记忆ID(doc_id)
        """
        if not content or not content.strip():
            raise ValueError("记忆内容不能为空")

        # 准备完整元数据 - 保存完整的 unified_msg_origin，不提取UUID
        # 只在查询/过滤时才提取UUID进行匹配，存储时保留完整信息
        current_time = time.time()
        full_metadata = {
            "session_id": session_id,  # 保存完整的 unified_msg_origin
            "persona_id": persona_id,  # 保存完整的 persona_id
            "importance": max(0.0, min(1.0, importance)),  # 限制在0-1范围
            "create_time": current_time,
            "last_access_time": current_time,
        }

        # 合并用户提供的额外元数据
        if metadata:
            full_metadata.update(metadata)

        # 通过混合检索器添加(会同时添加到BM25和向量索引)
        if self.hybrid_retriever is None:
            raise RuntimeError("混合检索器未初始化")
        doc_id = await self.hybrid_retriever.add_memory(content, full_metadata)

        return doc_id

    async def search_memories(
        self,
        query: str,
        k: int = 5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[HybridResult]:
        """
        检索相关记忆

        Args:
            query: 查询字符串
            k: 返回数量
            session_id: 会话ID过滤(可选,应传入unified_msg_origin完整格式)
            persona_id: 人格ID过滤(可选)

        Returns:
            List[HybridResult]: 检索结果列表
        """
        if not query or not query.strip():
            return []

        # 如果session_id是unified_msg_origin格式，自动触发旧数据迁移
        if session_id and ":" in session_id:
            # 异步触发迁移，不阻塞查询
            asyncio.create_task(self._migrate_session_data_if_needed(session_id))

        # 【关键修改】不再提取UUID，直接使用完整的unified_msg_origin进行匹配
        # 因为现在数据库中存储的就是完整格式
        # session_id 和 persona_id 保持原样传递给检索器

        # 执行混合检索
        if self.hybrid_retriever is None:
            raise RuntimeError("混合检索器未初始化")
        results = await self.hybrid_retriever.search(query, k, session_id, persona_id)

        # 异步更新访问时间(不阻塞返回)
        for result in results:
            asyncio.create_task(self._update_access_time_internal(result.doc_id))

        return results

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """
        根据ID获取记忆

        Args:
            memory_id: 记忆ID

        Returns:
            Optional[Dict]: 记忆数据,包含text和metadata
        """
        # 从faiss_db的document_storage获取文档
        try:
            # 使用 get_documents (复数) 并传入 ids 参数
            docs = await self.faiss_db.document_storage.get_documents(
                metadata_filters={}, ids=[memory_id], limit=1
            )

            if not docs or len(docs) == 0:
                return None

            doc = docs[0]
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
            from astrbot.api import logger

            logger.error(f"[更新] 记忆不存在 (memory_id={memory_id})")
            return False

        # 解析 metadata（可能是JSON字符串）
        current_metadata = memory.get("metadata", {})
        if isinstance(current_metadata, str):
            import json

            try:
                current_metadata = json.loads(current_metadata)
            except (json.JSONDecodeError, TypeError):
                current_metadata = {}
        elif not isinstance(current_metadata, dict):
            current_metadata = {}

        # 处理内容更新 (需要重建所有索引)
        if "content" in updates:
            new_content = updates["content"]
            if not new_content or not new_content.strip():
                return False

            try:
                from astrbot.api import logger

                # 保留必要信息
                session_id = current_metadata.get("session_id")
                persona_id = current_metadata.get("persona_id")
                importance = current_metadata.get(
                    "importance", updates.get("importance", 0.5)
                )

                # 构建新元数据
                new_metadata = current_metadata.copy()
                new_metadata["updated_at"] = time.time()
                new_metadata["previous_id"] = memory_id  # 记录旧ID

                # 【改进】先创建新记忆，再删除旧记忆（避免数据丢失）
                logger.info(f"[更新] 开始内容更新流程 (old_id={memory_id})")

                # 1. 创建新记忆（自动在所有数据库创建）
                new_memory_id = await self.add_memory(
                    content=new_content,
                    session_id=session_id,
                    persona_id=persona_id,
                    importance=importance,
                    metadata=new_metadata,
                )

                if new_memory_id is None:
                    logger.error(f"[更新] 创建新记忆失败 (old_id={memory_id})")
                    return False

                logger.info(f"[更新] 新记忆已创建 (new_id={new_memory_id})")

                # 2. 删除旧记忆（从所有数据库删除）
                delete_success = await self.delete_memory(memory_id)
                if not delete_success:
                    logger.warning(
                        f"[更新] 删除旧记忆失败，但新记忆已创建 (old_id={memory_id}, new_id={new_memory_id})"
                    )
                    # 不返回False，因为新记忆已经创建成功

                logger.info(
                    f"[更新] 内容更新完成 (old_id={memory_id} → new_id={new_memory_id})"
                )
                return True

            except Exception as e:
                from astrbot.api import logger

                logger.error(
                    f"[更新] 内容更新失败 (memory_id={memory_id}): {e}", exc_info=True
                )
                return False

        # 处理非内容的元数据更新（不需要重建索引）
        metadata_updates = {}

        if "importance" in updates:
            metadata_updates["importance"] = max(0.0, min(1.0, updates["importance"]))

        if "metadata" in updates:
            metadata_updates.update(updates["metadata"])

        if metadata_updates:
            from astrbot.api import logger

            # 确保 current_metadata 是字典（再次检查）
            if not isinstance(current_metadata, dict):
                import json

                try:
                    current_metadata = (
                        json.loads(current_metadata)
                        if isinstance(current_metadata, str)
                        else {}
                    )
                except (json.JSONDecodeError, TypeError):
                    current_metadata = {}

            # 合并元数据
            current_metadata.update(metadata_updates)
            current_metadata["updated_at"] = time.time()

            # 【改进】使用增强的update_metadata确保三库同步
            if self.hybrid_retriever is None:
                logger.error("混合检索器未初始化")
                return False
            success = await self.hybrid_retriever.update_metadata(
                memory_id, metadata_updates
            )

            if success:
                logger.info(f"[更新] 元数据更新成功 (memory_id={memory_id})")
            else:
                logger.error(f"[更新] 元数据更新失败 (memory_id={memory_id})")

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
        from astrbot.api import logger

        # 1. 通过混合检索器删除(会同时删除BM25和向量索引)
        if self.hybrid_retriever is None:
            logger.error("混合检索器未初始化")
            return False
        success = await self.hybrid_retriever.delete_memory(memory_id)

        if success:
            # 2. 同步删除SQLite documents表中的记录
            try:
                if self.db_connection is None:
                    logger.error("数据库连接未初始化")
                    return False
                await self.db_connection.execute(
                    "DELETE FROM documents WHERE id = ?", (memory_id,)
                )
                await self.db_connection.commit()
            except Exception as e:
                logger.warning(f"删除documents表失败 (memory_id={memory_id}): {e}")

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
        """内部方法:更新访问时间（直接更新documents表，不经过FAISS）"""
        import json

        current_time = time.time()

        try:
            if self.db_connection is None:
                return False

            # 直接更新 documents 表，不经过 FAISS
            # 1. 获取当前 metadata
            cursor = await self.db_connection.execute(
                "SELECT metadata FROM documents WHERE id = ?", (memory_id,)
            )
            row = await cursor.fetchone()

            if not row:
                return False

            # 2. 解析并更新 metadata
            metadata_str = row[0] if row[0] else "{}"
            try:
                metadata = (
                    json.loads(metadata_str)
                    if isinstance(metadata_str, str)
                    else metadata_str
                )
            except (json.JSONDecodeError, TypeError):
                metadata = {}

            metadata["last_access_time"] = current_time

            # 3. 写回 documents 表
            await self.db_connection.execute(
                "UPDATE documents SET metadata = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), memory_id),
            )
            await self.db_connection.commit()

            return True

        except Exception:
            # 静默失败，不影响查询流程
            return False

    async def get_session_memories(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        获取会话的所有记忆（使用分批处理和数据库排序优化）

        Args:
            session_id: 会话ID(应传入完整的unified_msg_origin格式)
            limit: 限制数量

        Returns:
            List[Dict]: 记忆列表
        """
        # 【关键修改】不再提取UUID，直接使用完整的session_id进行匹配
        # 因为现在数据库中存储的就是完整的unified_msg_origin格式

        # 使用数据库层面的排序和分页，避免加载所有数据
        try:
            # 先获取总数判断是否需要分批
            total_count = await self.faiss_db.document_storage.count_documents(
                metadata_filters={"session_id": session_id}
            )

            if total_count == 0:
                return []

            # 如果总数小于等于limit，直接一次性获取
            if total_count <= limit:
                all_docs = await self.faiss_db.document_storage.get_documents(
                    metadata_filters={"session_id": session_id},
                    limit=limit,
                    offset=0,
                )

                # 按创建时间排序
                sorted_docs = sorted(
                    all_docs,
                    key=lambda x: x["metadata"].get("create_time", 0),
                    reverse=True,
                )
            else:
                # 总数大于limit，需要分批加载所有数据进行排序（无法避免）
                # 但使用合理的批次大小来控制内存
                all_docs = []
                batch_size = 500
                offset = 0

                while offset < total_count:
                    batch = await self.faiss_db.document_storage.get_documents(
                        metadata_filters={"session_id": session_id},
                        limit=batch_size,
                        offset=offset,
                    )

                    if not batch:
                        break

                    all_docs.extend(batch)
                    offset += batch_size

                # 按创建时间排序并限制数量
                sorted_docs = sorted(
                    all_docs,
                    key=lambda x: x["metadata"].get("create_time", 0),
                    reverse=True,
                )[:limit]

            memories = []
            for doc in sorted_docs:
                memories.append(
                    {
                        "id": doc["id"],
                        "text": doc["text"],
                        "metadata": doc["metadata"],
                    }
                )

            return memories
        except Exception:
            return []

    async def cleanup_old_memories(
        self,
        days_threshold: int | None = None,
        importance_threshold: float | None = None,
    ) -> int:
        """
        清理旧记忆（使用分批处理避免内存问题）

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

        # 分批扫描文档并删除，避免一次性加载所有数据到内存
        try:
            # 先获取总数
            total_count = await self.faiss_db.document_storage.count_documents(
                metadata_filters={}
            )

            if total_count == 0:
                return 0

            deleted_count = 0
            batch_size = 500
            offset = 0

            # 分批处理
            while offset < total_count:
                # 获取一批文档
                batch_docs = await self.faiss_db.document_storage.get_documents(
                    metadata_filters={}, limit=batch_size, offset=offset
                )

                if not batch_docs:
                    break

                # 处理这批文档，找到需要删除的
                to_delete_in_batch = []

                for doc in batch_docs:
                    metadata = doc["metadata"]
                    # 处理 metadata（可能是JSON字符串或字典）
                    if isinstance(metadata, str):
                        try:
                            import json

                            metadata = json.loads(metadata)
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}
                    elif not isinstance(metadata, dict):
                        metadata = {}

                    create_time = metadata.get("create_time", time.time())
                    doc_importance = metadata.get("importance", 0.5)

                    # 确保时间值是数字类型
                    try:
                        create_time = float(create_time)
                        doc_importance = float(doc_importance)
                    except (ValueError, TypeError):
                        continue

                    if create_time < cutoff_time and doc_importance < importance:
                        to_delete_in_batch.append(doc["id"])

                # 删除这批中符合条件的记忆
                for memory_id in to_delete_in_batch:
                    success = await self.delete_memory(memory_id)
                    if success:
                        deleted_count += 1

                # 移动到下一批
                offset += batch_size

                # 如果这批数量少于batch_size，说明已经是最后一批
                if len(batch_docs) < batch_size:
                    break

            return deleted_count
        except Exception:
            return 0

    async def _migrate_session_data_if_needed(self, unified_msg_origin: str) -> None:
        """
        运行时自动迁移：将旧格式的session_id更新为unified_msg_origin格式

        支持各种平台的旧格式（通用匹配策略）：
        - WebChat UUID: "ac8c2cef-959e-4146-ad22-c82d0230ad06"
        - WebChat带前缀: "webchat!astrbot!ac8c2cef-959e-4146-ad22-c82d0230ad06"
        - QQ号: "123456789"
        - 其他平台: 任意字符串

        目标格式: "platform:message_type:session_id"

        策略：
        1. 从unified_msg_origin解析出：platform、message_type、session_id
        2. 生成所有可能的旧格式匹配候选（递归拆分）
        3. 查找匹配任一候选且不含冒号的旧记录
        4. 批量更新为unified_msg_origin
        5. 使用unified_msg_origin本身作为迁移标记（避免重复）

        Args:
            unified_msg_origin: 完整的统一消息来源（格式：platform:type:session_id）
        """

        try:
            # 1. 解析 unified_msg_origin
            parts = unified_msg_origin.split(":", 2)
            if len(parts) != 3:
                logger.warning(
                    "[自动迁移] unified_msg_origin 格式不正确: {unified_msg_origin}"
                )
                return

            platform_id, message_type, full_session_id = parts

            # 2. 生成所有可能的旧格式匹配候选
            # 对于 "webchat!astrbot!ac8c2cef-..." 会生成:
            #   ["webchat!astrbot!ac8c2cef-...", "astrbot!ac8c2cef-...", "ac8c2cef-..."]
            # 对于 "123456789" 会生成: ["123456789"]
            candidates = [full_session_id]

            # 按感叹号递归拆分
            if "!" in full_session_id:
                parts_by_bang = full_session_id.split("!")
                for i in range(1, len(parts_by_bang)):
                    candidates.append("!".join(parts_by_bang[i:]))

            logger.info(f"[自动迁移] 开始检查会话，候选匹配: {candidates}")

            # 3. 检查是否已迁移（使用unified_msg_origin本身作为标记）
            migration_key = f"migrated_umo_{unified_msg_origin}"
            cursor = await self.db_connection.execute(
                "SELECT value FROM migration_status WHERE key = ?", (migration_key,)
            )
            row = await cursor.fetchone()
            if row and row[0] == "true":
                # 已迁移过，跳过
                return

            # 4. 查找所有需要迁移的记录
            # 条件：session_id 匹配任一候选 且 不包含冒号（旧格式标识）
            placeholders = " OR ".join(
                ["json_extract(metadata, '$.session_id') = ?" for _ in candidates]
            )
            query = f"""
                SELECT id, metadata FROM documents
                WHERE ({placeholders})
                AND json_extract(metadata, '$.session_id') NOT LIKE '%:%'
            """

            cursor = await self.db_connection.execute(query, tuple(candidates))
            rows = await cursor.fetchall()

            if not rows:
                logger.info("[自动迁移] 未找到需要迁移的旧数据")
                # 即使没有旧数据也标记为已检查，避免重复查询
                await self.db_connection.execute(
                    "INSERT OR REPLACE INTO migration_status (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    (migration_key, "true"),
                )
                await self.db_connection.commit()
                return

            logger.info(f"[自动迁移] 找到 {len(rows)} 条旧数据需要迁移")

            # 5. 批量更新
            updated_count = 0
            for row in rows:
                doc_id = row[0]
                metadata_str = row[1]

                try:
                    metadata = json.loads(metadata_str) if metadata_str else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                old_session_id = metadata.get("session_id", "unknown")

                # 更新为unified_msg_origin格式
                metadata["session_id"] = unified_msg_origin
                metadata["migrated_at"] = time.time()
                metadata["old_session_id"] = old_session_id  # 保留旧值便于追溯

                # 写回数据库
                await self.db_connection.execute(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), doc_id),
                )
                updated_count += 1

            # 6. 提交更新
            await self.db_connection.commit()

            # 7. 标记为已迁移
            await self.db_connection.execute(
                "INSERT OR REPLACE INTO migration_status (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (migration_key, "true"),
            )
            await self.db_connection.commit()

            logger.info(
                f"[自动迁移] 完成！已更新 {updated_count} 条记录 -> {unified_msg_origin}"
            )

        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[自动迁移] 迁移失败: {e}", exc_info=True)

    async def get_statistics(self) -> dict[str, Any]:
        """
        获取记忆统计信息（使用批量处理避免内存问题）

        Returns:
            Dict: 统计信息,包含:
                - total_memories: 总记忆数
                - sessions: 各会话的记忆数（按UUID分组）
                - status_breakdown: 各状态的记忆数
                - avg_importance: 平均重要性
                - oldest_memory: 最旧记忆时间
                - newest_memory: 最新记忆时间
        """
        try:
            # 使用 count_documents() 高效获取总数（不加载数据）
            total_count = await self.faiss_db.document_storage.count_documents(
                metadata_filters={}
            )

            stats = {}
            stats["total_memories"] = total_count

            # 初始化统计变量
            session_counts: dict[str, int] = {}
            status_breakdown = {"active": 0, "archived": 0, "deleted": 0}
            importance_sum = 0
            importance_count = 0
            oldest_time = None
            newest_time = None

            # 分批处理，每次加载500条，避免内存问题
            batch_size = 500
            offset = 0

            while offset < total_count:
                # 获取一批文档
                batch_docs = await self.faiss_db.document_storage.get_documents(
                    metadata_filters={}, limit=batch_size, offset=offset
                )

                if not batch_docs:
                    break

                # 处理这批文档
                for doc in batch_docs:
                    # 处理 metadata（可能是JSON字符串或字典）
                    metadata = doc["metadata"]
                    if isinstance(metadata, str):
                        try:
                            import json

                            metadata = json.loads(metadata)
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}
                    elif not isinstance(metadata, dict):
                        metadata = {}

                    # 统计会话（直接使用session_id分组）
                    session_id = metadata.get("session_id")
                    if session_id:
                        session_counts[session_id] = (
                            session_counts.get(session_id, 0) + 1
                        )

                    # 统计状态（默认 active）
                    status = metadata.get("status", "active")
                    if status in status_breakdown:
                        status_breakdown[status] += 1
                    else:
                        # 未知状态默认计入 active
                        status_breakdown["active"] += 1

                    # 统计重要性
                    importance = metadata.get("importance")
                    if importance is not None:
                        try:
                            importance = float(importance)
                            importance_sum += importance
                            importance_count += 1
                        except (ValueError, TypeError):
                            pass

                    # 统计时间
                    create_time = metadata.get("create_time")
                    if create_time:
                        try:
                            create_time = float(create_time)
                            if oldest_time is None or create_time < oldest_time:
                                oldest_time = create_time
                            if newest_time is None or create_time > newest_time:
                                newest_time = create_time
                        except (ValueError, TypeError):
                            pass

                # 移动到下一批
                offset += batch_size

            stats["sessions"] = session_counts
            stats["status_breakdown"] = status_breakdown
            stats["avg_importance"] = (
                importance_sum / importance_count if importance_count > 0 else 0.0
            )
            stats["oldest_memory"] = oldest_time
            stats["newest_memory"] = newest_time

            return stats
        except Exception as e:
            from astrbot.api import logger

            logger.error(f"获取统计信息失败: {e}", exc_info=True)
            return {
                "total_memories": 0,
                "sessions": {},
                "status_breakdown": {"active": 0, "archived": 0, "deleted": 0},
                "avg_importance": 0.0,
                "oldest_memory": None,
                "newest_memory": None,
            }

# -*- coding: utf-8 -*-
"""
FaissManager - 高级数据库管理器
封装 FaissVecDB，提供支持动态记忆生命周期的接口。
"""

import time
import json
from typing import List, Dict, Any, Optional, Union
import numpy as np
from datetime import datetime

from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB, Result
from astrbot.api import logger
from ..core.utils import safe_parse_metadata, safe_serialize_metadata, validate_timestamp
from ..core.utils.stopwords_manager import StopwordsManager

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

class DateTimeEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，用于处理 datetime 对象"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class FaissManager:
    """
    一个高级管理器，封装了 FaissVecDB 的操作，并增加了对动态记忆生命周期的支持，
    包括重要性、新近度和访问时间的管理。
    """

    def __init__(
        self,
        db: FaissVecDB,
        sparse_retriever: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        """
        初始化 FaissManager。

        Args:
            db (FaissVecDB): 已经实例化的 FaissVecDB 对象。
            sparse_retriever: 稀疏检索器实例（用于文档同步）
            config: 配置字典
        """
        self.db = db
        self.datetime_encoder = DateTimeEncoder()
        self.sparse_retriever = sparse_retriever

        # 稠密检索预处理配置
        self.config = config or {}
        dense_config = self.config.get("dense_retriever", {})
        self.enable_query_preprocessing = dense_config.get("enable_query_preprocessing", True)
        self.stopwords_manager: Optional[StopwordsManager] = None

        logger.debug(f"FaissManager 初始化: 查询预处理={'启用' if self.enable_query_preprocessing else '禁用'}")

    async def initialize_stopwords(self, stopwords_manager: StopwordsManager):
        """
        设置停用词管理器（由插件主类调用）

        Args:
            stopwords_manager: 停用词管理器实例
        """
        self.stopwords_manager = stopwords_manager
        logger.info(f"FaissManager 已设置停用词管理器")

    async def add_memory(
        self,
        content: str,
        importance: float,
        session_id: str,
        persona_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        添加一条新的记忆到数据库。

        Args:
            content (str): 记忆的文本内容。
            importance (float): 记忆的重要性评分 (0.0 to 1.0)。
            session_id (str): 当前的会话 ID。
            persona_id (Optional[str], optional): 当前的人格 ID. Defaults to None.
            metadata (Optional[Dict[str, Any]], optional): 完整的事件元数据. Defaults to None.

        Returns:
            int: 插入的记忆在数据库中的主键 ID。
        """
        logger.debug(f"准备添加记忆: 长度={len(content)}, 重要性={importance:.3f}, session={session_id}")

        try:
            # 如果传入了完整的 metadata (来自新的 Event-based 流程)，直接使用
            if metadata:
                # 确保基础字段存在
                metadata.setdefault("importance", importance)
                metadata.setdefault("session_id", session_id)
                metadata.setdefault("persona_id", persona_id)

                # 时间戳现在应该是 datetime 对象，直接转换为 float
                ts_obj = metadata.get("timestamp")
                if ts_obj and hasattr(ts_obj, "timestamp"):
                    timestamp_float = ts_obj.timestamp()
                    metadata["create_time"] = timestamp_float
                    metadata["last_access_time"] = timestamp_float
                    logger.debug(f"  使用事件时间戳: {timestamp_float}")
                else:
                    # 后备方案
                    current_timestamp = time.time()
                    metadata.setdefault("create_time", current_timestamp)
                    metadata.setdefault("last_access_time", current_timestamp)
                    logger.debug(f"  使用当前时间戳: {current_timestamp}")
            else:
                # 兼容旧的或简单的调用方式
                current_timestamp = time.time()
                metadata = {
                    "importance": importance,
                    "create_time": current_timestamp,
                    "last_access_time": current_timestamp,
                    "session_id": session_id,
                    "persona_id": persona_id,
                }
                logger.debug(f"  创建简单元数据，时间戳: {current_timestamp}")

            # 确保元数据中的所有 datetime 对象都被序列化为字符串
            serialized_metadata = json.loads(self.datetime_encoder.encode(metadata))

            inserted_id = await self.db.insert(content=content, metadata=serialized_metadata)
            logger.info(f"✅ 成功添加记忆 ID={inserted_id}, 内容长度={len(content)}, 重要性={importance:.3f}")

            # 同步到稀疏检索索引
            if self.sparse_retriever:
                try:
                    await self.sparse_retriever.add_document(inserted_id, content)
                    logger.debug(f"已同步到稀疏索引: ID={inserted_id}")
                except Exception as e:
                    logger.warning(f"同步到稀疏索引失败: {e}")

            return inserted_id

        except Exception as e:
            logger.error(
                f"❌ 添加记忆失败: {type(e).__name__}: {e}",
                exc_info=True
            )
            logger.error(f"  失败上下文: content_len={len(content)}, importance={importance}, session={session_id}")
            raise

    def _preprocess_query(self, query: str) -> str:
        """
        预处理查询文本（用于稠密检索）

        Args:
            query: 原始查询

        Returns:
            str: 预处理后的查询
        """
        if not self.enable_query_preprocessing:
            return query

        if not query or not query.strip():
            return query

        # 1. 移除多余空白
        text = " ".join(query.split())

        # 2. 中文分词
        if JIEBA_AVAILABLE:
            if any('\u4e00' <= char <= '\u9fff' for char in text):
                tokens = list(jieba.cut(text))  # 使用普通分词，不是搜索引擎模式
            else:
                tokens = text.split()
        else:
            tokens = text.split()

        # 3. 去除停用词
        if self.stopwords_manager:
            filtered_tokens = []
            for token in tokens:
                # 跳过空token和纯标点
                if not token or token.isspace():
                    continue
                if all(not c.isalnum() for c in token):
                    continue
                # 跳过停用词
                if not self.stopwords_manager.is_stopword(token):
                    filtered_tokens.append(token)
            tokens = filtered_tokens
        else:
            tokens = [
                t for t in tokens
                if t and not t.isspace() and any(c.isalnum() for c in t)
            ]

        # 4. 重组为字符串
        processed = " ".join(tokens)

        if processed != query:
            logger.debug(f"查询预处理: '{query[:50]}...' -> '{processed[:50]}...'")

        return processed if processed else query  # 如果处理后为空，返回原查询

    async def search_memory(
        self,
        query: str,
        k: int,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[Result]:
        """
        根据查询文本智能检索最相关的记忆。

        Args:
            query (str): 查询文本。
            k (int): 希望返回的记忆数量。
            session_id (Optional[str], optional): 会话 ID 过滤器. Defaults to None.
            persona_id (Optional[str], optional): 人格 ID 过滤器. Defaults to None.

        Returns:
            List[Result]: 检索到的记忆列表。
        """
        # 预处理查询
        processed_query = self._preprocess_query(query)

        logger.debug(f"搜索记忆: query='{processed_query[:50]}...', k={k}, session={session_id or '无'}, persona={persona_id or '无'}")

        try:
            metadata_filters = {}
            if session_id:
                metadata_filters["session_id"] = session_id
            if persona_id:
                metadata_filters["persona_id"] = persona_id

            if metadata_filters:
                logger.debug(f"  应用过滤器: {metadata_filters}")

            # 从数据库检索，fetch_k 可以设置得比 k 大，以便有更多结果用于后续处理
            results = await self.db.retrieve(
                query=processed_query, k=k, fetch_k=k * 2, metadata_filters=metadata_filters
            )

            logger.debug(f"  检索返回 {len(results)} 条结果")

            if results:
                # 更新被访问记忆的 last_access_time
                # FaissVecDB 返回的 Result.data['id'] 才是我们需要的整数 ID
                accessed_ids = [res.data["id"] for res in results]
                logger.debug(f"  更新 {len(accessed_ids)} 条记忆的访问时间")
                await self.update_memory_access_time(accessed_ids)

            return results

        except Exception as e:
            logger.error(
                f"❌ 搜索记忆失败: {type(e).__name__}: {e}",
                exc_info=True
            )
            logger.error(f"  失败上下文: query='{query[:50]}...', k={k}")
            return []

    async def update_memory_access_time(self, doc_ids: List[int]):
        """
        批量更新一组记忆的最后访问时间。使用批量更新避免N+1查询问题。

        Args:
            doc_ids (List[int]): 需要更新的文档 ID 列表。
        """
        if not doc_ids:
            return

        current_timestamp = time.time()

        try:
            # 从数据库中获取现有的元数据
            docs = await self.db.document_storage.get_documents(
                ids=doc_ids, metadata_filters={}
            )

            if not docs:
                return

            # 准备批量更新数据
            batch_updates = []
            for doc in docs:
                try:
                    # 使用统一的元数据处理函数
                    metadata = safe_parse_metadata(doc["metadata"])
                    metadata["last_access_time"] = current_timestamp
                    
                    # 添加到批量更新列表
                    batch_updates.append((
                        safe_serialize_metadata(metadata), 
                        doc["id"]
                    ))
                except Exception as e:
                    logger.warning(f"处理文档 {doc['id']} 的元数据时出错: {e}")
                    continue

            # 执行批量更新
            if batch_updates:
                await self.db.document_storage.connection.executemany(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    batch_updates
                )
                await self.db.document_storage.connection.commit()
                logger.debug(f"成功批量更新 {len(batch_updates)} 个文档的访问时间")
                
        except Exception as e:
            logger.error(f"批量更新访问时间失败: {e}")
            # 回滚事务
            try:
                await self.db.document_storage.connection.rollback()
            except Exception as rollback_error:
                logger.error(f"回滚事务失败: {rollback_error}")

    async def get_all_memories_for_forgetting(self) -> List[Dict[str, Any]]:
        """
        获取所有记忆及其元数据，用于遗忘代理的处理。
        
        注意：此方法仅为向后兼容保留，新代码应使用 get_memories_paginated
        
        Returns:
            List[Dict[str, Any]]: 包含所有记忆数据的列表。
        """
        return await self.db.document_storage.get_documents(metadata_filters={})
    
    async def get_memories_paginated(
        self,
        page_size: int = 1000,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        分页获取记忆数据，避免一次性加载大量数据。

        Args:
            page_size: 每页记录数
            offset: 偏移量

        Returns:
            List[Dict[str, Any]]: 分页记忆数据,metadata 已解析为字典
        """
        try:
            # 修复: 明确指定列名,避免依赖列顺序
            # 注意: documents 表的文本列名是 'text' 而非 'content'
            async with self.db.document_storage.connection.execute(
                "SELECT id, text, metadata FROM documents ORDER BY id LIMIT ? OFFSET ?",
                (page_size, offset)
            ) as cursor:
                rows = await cursor.fetchall()

            # 修复: 解析 metadata JSON 为字典
            memories = []
            for row in rows:
                metadata_str = row[2] if row[2] else "{}"
                try:
                    # 如果已经是字典就直接用,否则解析JSON
                    metadata_dict = (
                        json.loads(metadata_str)
                        if isinstance(metadata_str, str)
                        else metadata_str
                    )
                except json.JSONDecodeError:
                    logger.warning(f"记忆 {row[0]} 的 metadata 解析失败,使用空字典")
                    metadata_dict = {}

                memory = {
                    "id": row[0],
                    "content": row[1],  # 从 'text' 列读取但返回为 'content' 字段
                    "metadata": metadata_dict  # ✅ 返回字典而非字符串
                }
                memories.append(memory)

            return memories

        except Exception as e:
            logger.error(f"分页获取记忆失败: {e}", exc_info=True)
            return []
    
    async def count_total_memories(self) -> int:
        """
        获取记忆总数。
        
        Returns:
            int: 记忆总数
        """
        try:
            async with self.db.document_storage.connection.execute(
                "SELECT COUNT(*) FROM documents"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"获取记忆总数失败: {e}")
            return 0

    async def update_memories_metadata(self, memories: List[Dict[str, Any]]):
        """
        批量更新记忆的元数据。使用executemany优化性能。
        """
        if not memories:
            return

        try:
            # 准备批量更新数据
            batch_updates = []
            for mem in memories:
                try:
                    batch_updates.append((
                        safe_serialize_metadata(mem["metadata"]), 
                        mem["id"]
                    ))
                except Exception as e:
                    logger.warning(f"处理记忆 {mem.get('id')} 的元数据时出错: {e}")
                    continue
            
            # 执行批量更新
            if batch_updates:
                await self.db.document_storage.connection.executemany(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    batch_updates
                )
                await self.db.document_storage.connection.commit()
                logger.debug(f"成功批量更新 {len(batch_updates)} 个记忆的元数据")
                
        except Exception as e:
            logger.error(f"批量更新记忆元数据失败: {e}")
            try:
                await self.db.document_storage.connection.rollback()
            except Exception as rollback_error:
                logger.error(f"回滚事务失败: {rollback_error}")
            raise

    async def delete_memories(self, doc_ids: List[int]):
        """
        批量删除一组记忆，使用改进的事务策略确保数据一致性。

        Args:
            doc_ids (List[int]): 需要删除的文档 ID 列表。

        Raises:
            RuntimeError: 删除失败时抛出异常
        """
        if not doc_ids:
            return

        # 开始事务
        await self.db.document_storage.connection.execute("BEGIN")

        faiss_backup_needed = False
        sqlite_deleted = False

        try:
            # 步骤1: 先从 SQLite 中删除（支持回滚）
            placeholders = ",".join("?" for _ in doc_ids)
            sql = f"DELETE FROM documents WHERE id IN ({placeholders})"
            await self.db.document_storage.connection.execute(sql, doc_ids)
            sqlite_deleted = True

            # 步骤2: 从 Faiss 索引中删除（不支持回滚）
            try:
                self.db.embedding_storage.index.remove_ids(np.array(doc_ids, dtype=np.int64))
                await self.db.embedding_storage.save_index()
                faiss_backup_needed = True
            except Exception as faiss_error:
                # Faiss操作失败，回滚SQLite
                logger.error(f"Faiss索引删除失败: {faiss_error}")
                await self.db.document_storage.connection.rollback()
                raise RuntimeError(f"Faiss索引删除失败，已回滚数据库操作: {faiss_error}") from faiss_error

            # 步骤3: 提交事务
            await self.db.document_storage.connection.commit()
            logger.info(f"成功删除 {len(doc_ids)} 条记忆")

            # 步骤4: 从稀疏索引删除
            if self.sparse_retriever:
                try:
                    for doc_id in doc_ids:
                        await self.sparse_retriever.delete_document(doc_id)
                    logger.debug(f"已从稀疏索引删除 {len(doc_ids)} 条记忆")
                except Exception as sparse_error:
                    logger.warning(f"从稀疏索引删除失败: {sparse_error}")

        except Exception as e:
            logger.error(f"删除记忆时发生错误: {e}", exc_info=True)

            # 回滚SQLite事务（如果尚未提交）
            if not sqlite_deleted or not faiss_backup_needed:
                try:
                    await self.db.document_storage.connection.rollback()
                    logger.info("已回滚数据库事务")
                except Exception as rollback_error:
                    logger.error(f"回滚事务失败: {rollback_error}")
            else:
                # SQLite和Faiss都已修改但提交失败 - 严重不一致
                logger.critical(
                    f"数据不一致警告: SQLite和Faiss索引都已修改但事务提交失败。"
                    f"受影响的ID: {doc_ids}。强烈建议执行 /lmem sparse_rebuild 重建索引。"
                )

            raise RuntimeError(f"删除记忆失败: {e}") from e

    async def wipe_all_memories(self) -> int:
        """
        清空所有记忆记录，返回删除的数量。
        """
        async with self.db.document_storage.connection.execute(
            "SELECT id FROM documents"
        ) as cursor:
            rows = await cursor.fetchall()

        doc_ids = [row[0] for row in rows]
        if not doc_ids:
            return 0

        await self.delete_memories(doc_ids)
        return len(doc_ids)

    async def update_memory(
        self,
        memory_id: Union[int, str],
        content: Optional[str] = None,
        importance: Optional[float] = None,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
        update_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        更新记忆内容或元数据（完全事务性操作）。

        Args:
            memory_id (Union[int, str]): 记忆ID（整数或UUID）
            content (Optional[str]): 新的记忆内容，如果提供则重新计算向量
            importance (Optional[float]): 新的重要性评分 (0.0-1.0)
            event_type (Optional[str]): 新的事件类型
            status (Optional[str]): 新的状态 (active/archived/deleted)
            update_reason (Optional[str]): 更新原因，用于记录

        Returns:
            Dict[str, Any]: 更新结果，包含 success、message、updated_fields 等信息
        """
        # 开始事务
        await self.db.document_storage.connection.execute("BEGIN")
        
        try:
            # 获取原始记忆
            if isinstance(memory_id, int):
                docs = await self.db.document_storage.get_documents(ids=[memory_id])
            else:
                # 如果是UUID，需要查询
                docs = await self.db.document_storage.get_documents(
                    metadata_filters={"memory_id": memory_id}
                )
            
            if not docs:
                await self.db.document_storage.connection.rollback()
                return {
                    "success": False,
                    "message": f"未找到ID为 {memory_id} 的记忆",
                    "updated_fields": []
                }
            
            original_doc = docs[0]
            try:
                original_metadata = (
                    json.loads(original_doc["metadata"])
                    if isinstance(original_doc["metadata"], str)
                    else original_doc["metadata"]
                )
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"解析原始记忆元数据时出错: {e}")
                return {
                    "success": False,
                    "message": f"解析记忆元数据失败: {str(e)}",
                    "updated_fields": []
                }
            
            # 准备更新数据
            updated_metadata = original_metadata.copy()
            updated_fields = []
            
            # 1. 更新内容和向量
            # 注意: documents表的文本列名是'text'而非'content'
            original_content = original_doc.get("text", original_doc.get("content", ""))
            if content is not None and content != original_content:
                # 重新计算向量
                embedding = await self.db.embedding_provider.embed_query(content)

                # 更新数据库 - 使用正确的列名'text'
                await self.db.document_storage.connection.execute(
                    "UPDATE documents SET text = ? WHERE id = ?",
                    (content, original_doc["id"]),
                )

                # 更新 Faiss 索引 - 先删除旧向量，再添加新向量（带ID关联）
                doc_id = original_doc["id"]
                self.db.embedding_storage.index.remove_ids(np.array([doc_id], dtype=np.int64))
                # 使用 add_with_ids 确保向量ID与文档ID一致
                self.db.embedding_storage.index.add_with_ids(
                    embedding.reshape(1, -1),
                    np.array([doc_id], dtype=np.int64)
                )
                await self.db.embedding_storage.save_index()

                updated_fields.append("content")

                # 同步更新稀疏索引
                if self.sparse_retriever and content:
                    try:
                        await self.sparse_retriever.update_document(doc_id, content)
                        logger.debug(f"已同步更新稀疏索引: ID={doc_id}")
                    except Exception as e:
                        logger.warning(f"同步更新稀疏索引失败: {e}")
            
            # 2. 更新元数据字段
            if importance is not None and importance != original_metadata.get("importance"):
                updated_metadata["importance"] = importance
                updated_fields.append("importance")
            
            if event_type is not None and event_type != original_metadata.get("event_type"):
                updated_metadata["event_type"] = event_type
                updated_fields.append("event_type")
            
            if status is not None and status != original_metadata.get("status", "active"):
                updated_metadata["status"] = status
                updated_fields.append("status")
            
            # 3. 记录更新历史
            if update_reason or updated_fields:
                update_history = updated_metadata.get("update_history", [])
                update_record = {
                    "timestamp": time.time(),
                    "reason": update_reason or "手动更新",
                    "fields": updated_fields.copy(),
                }
                update_history.append(update_record)
                updated_metadata["update_history"] = update_history
                updated_metadata["last_updated_time"] = time.time()
            
            # 4. 保存元数据更新
            if updated_fields:
                await self.db.document_storage.connection.execute(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    (json.dumps(updated_metadata), original_doc["id"]),
                )
                
                # 提交事务
                await self.db.document_storage.connection.commit()
                
                return {
                    "success": True,
                    "message": f"成功更新记忆 {memory_id}",
                    "updated_fields": updated_fields,
                    "memory_id": original_doc["id"]
                }
            else:
                return {
                    "success": True,
                    "message": "没有需要更新的字段",
                    "updated_fields": [],
                    "memory_id": original_doc["id"]
                }
                
        except Exception as e:
            logger.error(f"更新记忆时发生错误: {e}", exc_info=True)
            # 回滚事务
            try:
                await self.db.document_storage.connection.rollback()
            except Exception as rollback_error:
                logger.error(f"回滚事务失败: {rollback_error}")
            return {
                "success": False,
                "message": f"更新记忆时发生错误: {str(e)}",
                "updated_fields": [],
                "error": str(e)
            }

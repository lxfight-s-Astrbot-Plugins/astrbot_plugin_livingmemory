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

    def __init__(self, db: FaissVecDB):
        """
        初始化 FaissManager。

        Args:
            db (FaissVecDB): 已经实例化的 FaissVecDB 对象。
        """
        self.db = db
        self.datetime_encoder = DateTimeEncoder()

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
            else:
                # 后备方案
                current_timestamp = time.time()
                metadata.setdefault("create_time", current_timestamp)
                metadata.setdefault("last_access_time", current_timestamp)
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

        # 确保元数据中的所有 datetime 对象都被序列化为字符串
        serialized_metadata = json.loads(self.datetime_encoder.encode(metadata))
        
        inserted_id = await self.db.insert(content=content, metadata=serialized_metadata)
        return inserted_id

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
        metadata_filters = {}
        if session_id:
            metadata_filters["session_id"] = session_id
        if persona_id:
            metadata_filters["persona_id"] = persona_id

        # 从数据库检索，fetch_k 可以设置得比 k 大，以便有更多结果用于后续处理
        results = await self.db.retrieve(
            query=query, k=k, fetch_k=k * 2, metadata_filters=metadata_filters
        )

        if results:
            # 更新被访问记忆的 last_access_time
            # FaissVecDB 返回的 Result.data['id'] 才是我们需要的整数 ID
            accessed_ids = [res.data["id"] for res in results]
            await self.update_memory_access_time(accessed_ids)

        return results

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

            # 执行批量更新（显式事务）
            if batch_updates:
                await self.db.document_storage.connection.execute("BEGIN IMMEDIATE")
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
            List[Dict[str, Any]]: 分页记忆数据
        """
        try:
            # 使用 SQLite 的 LIMIT 和 OFFSET 进行分页
            async with self.db.document_storage.connection.execute(
                "SELECT * FROM documents ORDER BY id LIMIT ? OFFSET ?",
                (page_size, offset)
            ) as cursor:
                rows = await cursor.fetchall()
                
            # 转换为字典格式
            memories = []
            for row in rows:
                memory = {
                    "id": row[0],  # id 列
                    "text": row[1],  # text 列 (注意：AstrBot使用text列，不是content)
                    "metadata": row[2] if row[2] else "{}"  # metadata 列
                }
                memories.append(memory)
                
            return memories
            
        except Exception as e:
            logger.error(f"分页获取记忆失败: {e}")
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
            
            # 执行批量更新（显式事务）
            if batch_updates:
                await self.db.document_storage.connection.execute("BEGIN IMMEDIATE")
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
        批量删除一组记忆,使用事务和备份确保数据一致性。

        采用先备份Faiss向量,后删除的策略:
        1. 提取待删除ID的向量数据作为备份
        2. 开始SQLite事务
        3. 从SQLite删除记录
        4. 从Faiss索引删除向量
        5. 保存Faiss索引
        6. 提交SQLite事务
        7. 如果任何步骤失败,回滚SQLite并尝试恢复Faiss

        Args:
            doc_ids (List[int]): 需要删除的文档 ID 列表。
        """
        if not doc_ids:
            return

        # 备份向量数据(用于回滚)
        backup_vectors = {}
        try:
            for doc_id in doc_ids:
                try:
                    # 尝试从Faiss索引中提取向量
                    vector = self.db.embedding_storage.index.reconstruct(int(doc_id))
                    backup_vectors[doc_id] = vector
                except Exception as e:
                    logger.warning(f"无法备份文档 {doc_id} 的向量: {e}")
        except Exception as e:
            logger.warning(f"向量备份失败(将继续删除操作): {e}")

        # 开始事务（使用IMMEDIATE避免锁升级）
        await self.db.document_storage.connection.execute("BEGIN IMMEDIATE")

        sqlite_deleted = False
        faiss_deleted = False

        try:
            # 步骤1: 从 SQLite 中删除
            placeholders = ",".join("?" for _ in doc_ids)
            sql = f"DELETE FROM documents WHERE id IN ({placeholders})"
            await self.db.document_storage.connection.execute(sql, doc_ids)
            sqlite_deleted = True

            # 步骤2: 从 Faiss 索引中删除
            self.db.embedding_storage.index.remove_ids(np.array(doc_ids, dtype=np.int64))
            faiss_deleted = True

            # 步骤3: 保存Faiss索引
            await self.db.embedding_storage.save_index()

            # 步骤4: 提交SQLite事务
            await self.db.document_storage.connection.commit()
            logger.info(f"成功删除 {len(doc_ids)} 条记忆")

        except Exception as e:
            logger.error(f"删除记忆时发生错误: {e}", exc_info=True)

            # 回滚SQLite事务
            try:
                await self.db.document_storage.connection.rollback()
                logger.info("SQLite事务已回滚")
            except Exception as rollback_error:
                logger.error(f"SQLite回滚失败: {rollback_error}")

            # 尝试恢复Faiss索引
            if faiss_deleted and backup_vectors:
                try:
                    logger.warning("尝试恢复Faiss索引...")
                    for doc_id, vector in backup_vectors.items():
                        # 重新添加向量
                        vector_reshaped = vector.reshape(1, -1)
                        self.db.embedding_storage.index.add_with_ids(
                            vector_reshaped,
                            np.array([doc_id], dtype=np.int64)
                        )
                    await self.db.embedding_storage.save_index()
                    logger.info(f"Faiss索引已恢复 {len(backup_vectors)} 个向量")
                except Exception as restore_error:
                    logger.critical(
                        f"Faiss索引恢复失败,数据可能不一致,建议重建索引: {restore_error}"
                    )
            elif faiss_deleted:
                logger.critical(
                    "Faiss索引已删除但SQLite回滚,且无备份数据,建议重建索引以保证一致性"
                )

            raise RuntimeError(f"删除记忆失败: {e}") from e

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
        # 开始事务（使用IMMEDIATE避免锁升级）
        await self.db.document_storage.connection.execute("BEGIN IMMEDIATE")

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
            if content is not None and content != original_doc.get("text"):
                # 重新计算向量
                embedding = await self.db.embedding_provider.embed_query(content)

                # 更新数据库 (AstrBot使用text列，不是content)
                await self.db.document_storage.connection.execute(
                    "UPDATE documents SET text = ?, embedding = ? WHERE id = ?",
                    (content, embedding.tobytes(), original_doc["id"]),
                )

                # 更新 Faiss 索引 - 使用 add_with_ids 保留原ID
                self.db.embedding_storage.index.remove_ids(np.array([original_doc["id"]], dtype=np.int64))
                self.db.embedding_storage.index.add_with_ids(
                    embedding.reshape(1, -1),
                    np.array([original_doc["id"]], dtype=np.int64)
                )
                await self.db.embedding_storage.save_index()

                updated_fields.append("content")
            
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

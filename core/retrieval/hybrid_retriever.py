# -*- coding: utf-8 -*-
"""
混合检索器 - 结合BM25和向量检索的混合检索
实现并行检索、RRF融合和智能加权策略
"""

import asyncio
import json
import math
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .rrf_fusion import RRFFusion, FusedResult
from .bm25_retriever import BM25Retriever
from .vector_retriever import VectorRetriever
from astrbot.api import logger


@dataclass
class HybridResult:
    """混合检索结果"""

    doc_id: int
    final_score: float  # 加权后的最终分数
    rrf_score: float  # RRF融合分数
    bm25_score: Optional[float]  # BM25分数
    vector_score: Optional[float]  # 向量分数
    content: str
    metadata: Dict[str, Any]


class HybridRetriever:
    """
    混合检索器

    结合BM25稀疏检索和向量密集检索,通过RRF融合结果,
    并应用重要性和时间衰减加权策略。

    主要特性:
    1. 并行执行BM25和向量检索(使用asyncio.gather)
    2. 使用RRF算法融合两路结果
    3. 应用重要性加权和时间衰减
    4. 支持退化机制(某一路失败时使用另一路)
    5. 确保两个索引中doc_id的一致性
    """

    def __init__(
        self,
        bm25_retriever: BM25Retriever,
        vector_retriever: VectorRetriever,
        rrf_fusion: RRFFusion,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化混合检索器

        Args:
            bm25_retriever: BM25检索器实例
            vector_retriever: 向量检索器实例
            rrf_fusion: RRF融合器实例
            config: 配置字典,支持以下参数:
                - decay_rate: 时间衰减率,默认0.01
                - importance_weight: 重要性权重,默认1.0
                - fallback_enabled: 启用退化机制,默认True
        """
        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever
        self.rrf_fusion = rrf_fusion
        self.config = config or {}

        # 配置参数
        self.decay_rate = self.config.get("decay_rate", 0.01)
        self.importance_weight = self.config.get("importance_weight", 1.0)
        self.fallback_enabled = self.config.get("fallback_enabled", True)

    async def add_memory(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        添加记忆到两个索引

        Args:
            content: 记忆内容
            metadata: 元数据(必须包含:importance, create_time, last_access_time,
                     session_id, persona_id)

        Returns:
            int: 文档ID(两个索引中一致)
        """
        # 确保metadata存在
        metadata = metadata or {}

        # 补充默认元数据
        if "importance" not in metadata:
            metadata["importance"] = 0.5
        if "create_time" not in metadata:
            metadata["create_time"] = time.time()
        if "last_access_time" not in metadata:
            metadata["last_access_time"] = time.time()
        if "session_id" not in metadata:
            metadata["session_id"] = None
        if "persona_id" not in metadata:
            metadata["persona_id"] = None

        # 先添加到向量库获取doc_id
        doc_id = await self.vector_retriever.add_document(content, metadata)

        # 使用相同的doc_id添加到BM25索引
        await self.bm25_retriever.add_document(doc_id, content, metadata)

        return doc_id

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[HybridResult]:
        """
        执行混合检索

        Args:
            query: 查询字符串
            k: 返回的结果数量
            session_id: 会话ID过滤(可选)
            persona_id: 人格ID过滤(可选)

        Returns:
            List[HybridResult]: 混合检索结果,按最终分数降序排列
        """
        if not query or not query.strip():
            return []

        # 1. 并行执行两路检索
        bm25_results = None
        vector_results = None
        bm25_error = None
        vector_error = None

        try:
            # 使用asyncio.gather并行执行
            results = await asyncio.gather(
                self.bm25_retriever.search(query, k, session_id, persona_id),
                self.vector_retriever.search(query, k, session_id, persona_id),
                return_exceptions=True,
            )

            # 检查结果
            if isinstance(results[0], Exception):
                bm25_error = results[0]
                logger.error(f"BM25检索异常: {bm25_error}")
            else:
                bm25_results = results[0]

            if isinstance(results[1], Exception):
                vector_error = results[1]
                logger.error(f"向量检索异常: {vector_error}")
            else:
                vector_results = results[1]

        except Exception as e:
            # 如果整体失败,尝试单独执行
            if self.fallback_enabled:
                try:
                    bm25_results = await self.bm25_retriever.search(
                        query, k, session_id, persona_id
                    )
                except Exception as be:
                    bm25_error = be
                    logger.warning(f"BM25检索失败: {bm25_error}")

                try:
                    vector_results = await self.vector_retriever.search(
                        query, k, session_id, persona_id
                    )
                except Exception as ve:
                    vector_error = ve
                    logger.warning(f"向量检索失败: {vector_error}")

            else:
                raise e

        # 2. 处理退化情况
        if not bm25_results and not vector_results:
            # 两路都失败
            return []

        if not bm25_results and self.fallback_enabled and vector_results:
            # 只有向量结果,使用向量退化
            return self._fallback_vector_only(vector_results, k)

        if not vector_results and self.fallback_enabled and bm25_results:
            # 只有BM25结果,使用BM25退化
            return self._fallback_bm25_only(bm25_results, k)

        # 3. RRF融合
        fused_results = self.rrf_fusion.fuse(bm25_results, vector_results, top_k=k)

        if not fused_results:
            return []

        # 4. 应用加权
        current_time = time.time()
        weighted_results = self._apply_weighting(fused_results, current_time)

        return weighted_results

    def _apply_weighting(
        self, fused_results: List[FusedResult], current_time: float
    ) -> List[HybridResult]:
        """
        应用重要性和时间衰减加权

        Args:
            fused_results: RRF融合后的结果
            current_time: 当前时间戳

        Returns:
            List[HybridResult]: 加权后的结果,按最终分数降序排列
        """
        hybrid_results = []

        for result in fused_results:
            # 安全解析metadata，确保它是字典类型
            metadata = result.metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                    logger.debug(
                        f"[hybrid_retriever] 将字符串metadata转换为字典: doc_id={result.doc_id}"
                    )
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(
                        f"[hybrid_retriever] 解析metadata JSON失败: {e}, doc_id={result.doc_id}, "
                        f"metadata类型={type(metadata)}, 使用空字典"
                    )
                    metadata = {}
            elif metadata is None:
                logger.debug(
                    f"[hybrid_retriever] metadata为None, doc_id={result.doc_id}, 使用空字典"
                )
                metadata = {}
            elif not isinstance(metadata, dict):
                logger.warning(
                    f"[hybrid_retriever] metadata类型不支持: {type(metadata)}, doc_id={result.doc_id}, "
                    f"使用空字典"
                )
                metadata = {}

            # 获取重要性(默认0.5)
            importance = metadata.get("importance", 0.5)

            # 计算时间衰减
            create_time = metadata.get("create_time", current_time)
            days_old = (current_time - create_time) / 86400  # 转换为天数
            recency_weight = math.exp(-self.decay_rate * days_old)

            # 计算最终分数
            final_score = (
                result.rrf_score * importance * self.importance_weight * recency_weight
            )

            hybrid_results.append(
                HybridResult(
                    doc_id=result.doc_id,
                    final_score=final_score,
                    rrf_score=result.rrf_score,
                    bm25_score=result.bm25_score,
                    vector_score=result.vector_score,
                    content=result.content,
                    metadata=metadata,
                )
            )

        # 按最终分数降序排序
        hybrid_results.sort(key=lambda x: x.final_score, reverse=True)

        return hybrid_results

    def _fallback_bm25_only(self, bm25_results: List, k: int) -> List[HybridResult]:
        """
        BM25退化:仅使用BM25结果

        Args:
            bm25_results: BM25检索结果
            k: 返回的结果数量

        Returns:
            List[HybridResult]: 退化后的结果
        """
        # 将BM25结果转换为FusedResult
        fused_results = self.rrf_fusion._convert_bm25_only(bm25_results, k)

        # 应用加权
        current_time = time.time()
        return self._apply_weighting(fused_results, current_time)

    def _fallback_vector_only(self, vector_results: List, k: int) -> List[HybridResult]:
        """
        向量退化:仅使用向量结果

        Args:
            vector_results: 向量检索结果
            k: 返回的结果数量

        Returns:
            List[HybridResult]: 退化后的结果
        """
        # 将向量结果转换为FusedResult
        fused_results = self.rrf_fusion._convert_vector_only(vector_results, k)

        # 应用加权
        current_time = time.time()
        return self._apply_weighting(fused_results, current_time)

    async def update_metadata(self, doc_id: int, metadata: Dict[str, Any]) -> bool:
        """
        同步更新所有存储层的元数据

        确保FAISS向量库、documents表、BM25索引的元数据保持一致。
        采用"尽力而为"策略：至少FAISS必须成功，其他失败仅警告。

        Args:
            doc_id: 文档ID
            metadata: 新的元数据

        Returns:
            bool: FAISS更新是否成功
        """
        import aiosqlite
        import json

        success_count = 0
        error_messages = []

        try:
            # 1. 更新FAISS向量库（主存储，必须成功）
            vector_success = await self.vector_retriever.update_metadata(doc_id, metadata)

            if not vector_success:
                logger.error(f"[同步] FAISS向量库更新失败 (doc_id={doc_id})")
                return False

            success_count += 1
            logger.debug(f"[同步] ✓ FAISS向量库已更新 (doc_id={doc_id})")

            # 2. 更新documents表（辅助存储）
            try:
                async with aiosqlite.connect(self.bm25_retriever.db_path) as db:
                    cursor = await db.execute(
                        "SELECT metadata FROM documents WHERE id = ?", (doc_id,)
                    )
                    row = await cursor.fetchone()

                    if row:
                        current_metadata = json.loads(row[0]) if row[0] else {}
                        current_metadata.update(metadata)

                        await db.execute(
                            "UPDATE documents SET metadata = ? WHERE id = ?",
                            (json.dumps(current_metadata, ensure_ascii=False), doc_id),
                        )
                        await db.commit()
                        success_count += 1
                        logger.debug(f"[同步] ✓ documents表已更新 (doc_id={doc_id})")
                    else:
                        logger.debug(f"[同步] ⊘ documents表中无此记录 (doc_id={doc_id})")

            except Exception as e:
                error_messages.append(f"documents表更新失败: {e}")
                logger.warning(f"[同步] ✗ documents表更新失败 (doc_id={doc_id}): {e}")

            # 3. 【新增】BM25索引说明
            # 注意：BM25 FTS5索引只存储分词后的内容用于检索，不存储metadata
            # metadata是从documents表读取的，所以步骤2已经完成了BM25需要的更新
            logger.debug(f"[同步] ℹ BM25索引通过documents表间接更新 (doc_id={doc_id})")

            # 记录同步结果
            if error_messages:
                logger.warning(
                    f"[同步] 部分同步成功 ({success_count}/2): {', '.join(error_messages)}"
                )
            else:
                logger.info(f"[同步] 完全同步成功 (doc_id={doc_id})")

            return True

        except Exception as e:
            logger.error(f"[同步] 元数据更新失败 (doc_id={doc_id}): {e}")
            return False

    async def delete_memory(self, doc_id: int) -> bool:
        """
        从多个存储层中删除记忆

        Args:
            doc_id: 文档ID

        Returns:
            bool: 是否成功删除
        """
        import aiosqlite

        try:
            # 1. 并行删除向量库和BM25索引
            results = await asyncio.gather(
                self.bm25_retriever.delete_document(doc_id),
                self.vector_retriever.delete_document(doc_id),
                return_exceptions=True,
            )

            bm25_success = (
                results[0] if not isinstance(results[0], Exception) else False
            )
            vector_success = (
                results[1] if not isinstance(results[1], Exception) else False
            )

            if isinstance(results[0], Exception):
                logger.error(f"BM25删除异常: {results[0]}")
            if isinstance(results[1], Exception):
                logger.error(f"向量删除异常: {results[1]}")

            if not (bm25_success and vector_success):
                return False

            # 2. 删除documents表记录
            try:
                async with aiosqlite.connect(self.bm25_retriever.db_path) as db:
                    await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                    await db.commit()
            except Exception as e:
                logger.warning(f"删除documents表失败 (doc_id={doc_id}): {e}")

            return True

        except Exception as e:
            logger.error(f"删除记忆失败 (doc_id={doc_id}): {e}")
            return False

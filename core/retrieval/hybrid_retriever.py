"""
混合检索器 - 结合BM25和向量检索的混合检索
实现并行检索、RRF融合和智能加权策略
"""

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .bm25_retriever import BM25Retriever
from .rrf_fusion import BM25Result, FusedResult, RRFFusion, VectorResult
from .vector_retriever import VectorRetriever


@dataclass
class HybridResult:
    """混合检索结果"""

    doc_id: int
    final_score: float  # 加权后的最终分数
    rrf_score: float  # RRF融合分数
    bm25_score: float | None  # BM25分数
    vector_score: float | None  # 向量分数
    content: str
    metadata: dict[str, Any]
    score_breakdown: dict[str, float] | None = None  # 各维度分数明细


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
        config: dict[str, Any] | None = None,
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

        # 加权求和各维度权重（可通过配置覆盖）
        self.score_alpha = self.config.get("score_alpha", 0.5)   # 检索相关性
        self.score_beta = self.config.get("score_beta", 0.25)    # 重要性
        self.score_gamma = self.config.get("score_gamma", 0.25)  # 时间新鲜度

        # MMR 多样性参数
        self.mmr_lambda = self.config.get("mmr_lambda", 0.7)     # 相关性 vs 多样性权衡

    async def add_memory(
        self, content: str, metadata: dict[str, Any] | None = None
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
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[HybridResult]:
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
            if not isinstance(vector_results, BaseException):
                return self._fallback_vector_only(vector_results, k)
            return []

        if not vector_results and self.fallback_enabled and bm25_results:
            # 只有BM25结果,使用BM25退化
            if not isinstance(bm25_results, BaseException):
                return self._fallback_bm25_only(bm25_results, k)
            return []

        # 3. RRF融合
        # 确保结果不是异常
        valid_bm25 = (
            bm25_results if not isinstance(bm25_results, BaseException) else None
        )
        valid_vector = (
            vector_results if not isinstance(vector_results, BaseException) else None
        )

        if valid_bm25 is None or valid_vector is None:
            return []

        # 转换结果类型以匹配RRF融合器期望的类型
        rrf_bm25_results = [
            BM25Result(
                doc_id=r.doc_id, score=r.score, content=r.content, metadata=r.metadata
            )
            for r in valid_bm25
        ]

        rrf_vector_results = [
            VectorResult(
                doc_id=r.doc_id, score=r.score, content=r.content, metadata=r.metadata
            )
            for r in valid_vector
        ]

        fused_results = self.rrf_fusion.fuse(
            rrf_bm25_results, rrf_vector_results, top_k=k
        )

        if not fused_results:
            return []

        # 4. 应用加权
        current_time = time.time()
        weighted_results = self._apply_weighting(fused_results, current_time)

        # 5. MMR 去重：在保证相关性的同时提升结果多样性
        if len(weighted_results) > 1:
            weighted_results = self._apply_mmr(weighted_results, k)

        return weighted_results

    def _apply_weighting(
        self, fused_results: list[FusedResult], current_time: float
    ) -> list[HybridResult]:
        """
        应用重要性和时间衰减加权

        使用加权求和（而非乘法）避免任何单一维度低分导致整体清零。
        时间衰减基于 max(create_time, last_access_time)，高频访问记忆衰减更慢。

        Args:
            fused_results: RRF融合后的结果
            current_time: 当前时间戳

        Returns:
            List[HybridResult]: 加权后的结果,按最终分数降序排列
        """
        if not fused_results:
            return []

        # 先归一化 RRF 分数到 [0, 1]
        max_rrf = max(r.rrf_score for r in fused_results)
        if max_rrf <= 0:
            max_rrf = 1.0

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

            # 获取重要性(默认0.5)，限制在 [0, 1]
            importance = max(0.0, min(1.0, metadata.get("importance", 0.5)))

            # 时间衰减：取 create_time 与 last_access_time 的较大值
            # 高频访问的记忆衰减更慢，符合"记忆强化"认知规律
            create_time = metadata.get("create_time", current_time)
            last_access_time = metadata.get("last_access_time", 0)
            reference_time = max(create_time, last_access_time)
            days_old = max(0.0, (current_time - reference_time) / 86400)
            recency_weight = math.exp(-self.decay_rate * days_old)

            # 归一化 RRF 分数
            rrf_normalized = result.rrf_score / max_rrf

            # 加权求和：各维度互补而非互斥
            final_score = (
                self.score_alpha * rrf_normalized
                + self.score_beta * importance
                + self.score_gamma * recency_weight
            )

            score_breakdown = {
                "rrf_normalized": round(rrf_normalized, 4),
                "importance": round(importance, 4),
                "recency_weight": round(recency_weight, 4),
                "days_old": round(days_old, 2),
                "final_score": round(final_score, 4),
            }

            hybrid_results.append(
                HybridResult(
                    doc_id=result.doc_id,
                    final_score=final_score,
                    rrf_score=result.rrf_score,
                    bm25_score=result.bm25_score,
                    vector_score=result.vector_score,
                    content=result.content,
                    metadata=metadata,
                    score_breakdown=score_breakdown,
                )
            )

        # 按最终分数降序排序
        hybrid_results.sort(key=lambda x: x.final_score, reverse=True)

        return hybrid_results

    def _apply_mmr(self, results: list[HybridResult], k: int) -> list[HybridResult]:
        """
        最大边际相关性（MMR）去重，避免多条语义重复的记忆占据 Top-K。

        使用内容词袋相似度作为轻量代理（无需额外向量计算）。
        mmr_lambda 越高越偏向相关性，越低越偏向多样性。

        Args:
            results: 已按 final_score 降序排列的候选结果
            k: 最终返回数量

        Returns:
            List[HybridResult]: 去重后的结果
        """
        if len(results) <= k:
            return results

        def _token_set(text: str) -> set[str]:
            tokens = set(text.lower().split())
            return tokens if tokens else {"<empty>"}

        selected: list[HybridResult] = []
        candidates = list(results)

        while candidates and len(selected) < k:
            if not selected:
                # 第一条直接选最高分
                selected.append(candidates.pop(0))
                continue

            best_idx = -1
            best_mmr = -1.0
            selected_tokens = [_token_set(s.content) for s in selected]

            for i, cand in enumerate(candidates):
                cand_tokens = _token_set(cand.content)
                # 与已选结果的最大 Jaccard 相似度
                max_sim = max(
                    len(cand_tokens & st) / max(len(cand_tokens | st), 1)
                    for st in selected_tokens
                )
                mmr_score = (
                    self.mmr_lambda * cand.final_score
                    - (1 - self.mmr_lambda) * max_sim
                )
                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_idx = i

            if best_idx >= 0:
                selected.append(candidates.pop(best_idx))
            else:
                break

        return selected

    def _fallback_bm25_only(self, bm25_results: list, k: int) -> list[HybridResult]:
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

    def _fallback_vector_only(self, vector_results: list, k: int) -> list[HybridResult]:
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

    async def update_metadata(self, doc_id: int, metadata: dict[str, Any]) -> bool:
        """
        同步更新所有存储层的元数据

        ID体系说明：
        - doc_id (int): documents表的主键，统一标识符
        - 三个存储层都使用这个整数ID进行关联
        - FAISS内部使用UUID，但对外接口使用整数ID

        更新策略：
        1. FAISS向量库（通过vector_retriever，会更新DocumentStorage）
        2. documents表无需额外更新（已在步骤1完成）
        3. BM25索引不存储metadata，从documents表读取

        Args:
            doc_id: 文档ID (整数)
            metadata: 新的元数据字典

        Returns:
            bool: 是否更新成功
        """
        try:
            # 更新FAISS向量库（这会同步更新DocumentStorage中的metadata）
            vector_success = await self.vector_retriever.update_metadata(
                doc_id, metadata
            )

            if not vector_success:
                logger.error(f"[同步更新] FAISS更新失败 (doc_id={doc_id})")
                return False

            logger.info(f"[同步更新] 元数据更新成功 (doc_id={doc_id})")
            return True

        except Exception as e:
            logger.error(f"[同步更新] 失败 (doc_id={doc_id}): {e}", exc_info=True)
            return False

    async def delete_memory(self, doc_id: int) -> bool:
        """
        从多个存储层中删除记忆（带事务回滚机制）

        Args:
            doc_id: 文档ID

        Returns:
            bool: 是否成功删除
        """
        import aiosqlite

        backup_content: str | None = None
        backup_metadata: dict[str, Any] = {}

        try:
            # Backup the original document so BM25 can be restored on failure.
            try:
                async with aiosqlite.connect(self.bm25_retriever.db_path) as db:
                    cursor = await db.execute(
                        "SELECT text, metadata FROM documents WHERE id = ?", (doc_id,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        backup_content = row[0]
                        metadata_raw = row[1]
                        if isinstance(metadata_raw, str) and metadata_raw:
                            try:
                                backup_metadata = json.loads(metadata_raw)
                            except (json.JSONDecodeError, TypeError):
                                backup_metadata = {}
                        elif isinstance(metadata_raw, dict):
                            backup_metadata = metadata_raw
            except Exception as e:
                logger.warning(f"[删除] 备份文档内容失败 (doc_id={doc_id}): {e}")

            # 优化1: 先删除BM25索引（外键引用）
            try:
                bm25_deleted = await self.bm25_retriever.delete_document(doc_id)
                if not bm25_deleted:
                    logger.warning(f"[删除] BM25索引删除失败 (doc_id={doc_id})")
                    return False
                logger.debug(f"[删除] BM25索引已删除 (doc_id={doc_id})")
            except Exception as e:
                logger.error(f"[删除] BM25删除异常 (doc_id={doc_id}): {e}")
                return False

            # 再删除向量库（主数据）
            try:
                vector_deleted = await self.vector_retriever.delete_document(doc_id)
                if not vector_deleted:
                    logger.error(f"[删除] 向量库删除失败，需回滚 (doc_id={doc_id})")
                    # 回滚: 恢复BM25索引
                    await self._rollback_bm25_delete(
                        doc_id, backup_content, backup_metadata
                    )
                    return False
                logger.debug(f"[删除] 向量库已删除 (doc_id={doc_id})")
            except Exception as e:
                logger.error(f"[删除] 向量删除异常，回滚BM25 (doc_id={doc_id}): {e}")
                # 回滚: 恢复BM25索引
                await self._rollback_bm25_delete(
                    doc_id, backup_content, backup_metadata
                )
                return False

            # 最后删除documents表记录
            try:
                async with aiosqlite.connect(self.bm25_retriever.db_path) as db:
                    await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                    await db.commit()
                logger.debug(f"[删除] documents表已删除 (doc_id={doc_id})")
            except Exception as e:
                logger.warning(f"[删除] documents表删除失败 (doc_id={doc_id}): {e}")
                # documents表删除失败不影响整体，因为主要数据已删除

            logger.info(f"[删除] 记忆删除成功 (doc_id={doc_id})")
            return True

        except Exception as e:
            logger.error(f"[删除] 删除记忆失败 (doc_id={doc_id}): {e}", exc_info=True)
            return False

    async def _rollback_bm25_delete(
        self,
        doc_id: int,
        content: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        回滚BM25删除操作（尽力而为，实际恢复索引）

        Args:
            doc_id: 文档ID
            content: Backed-up document text captured before deletion
            metadata: 文档元数据（可选）
        """
        if not content:
            logger.error(
                f"[回滚] 缺少备份文本，无法恢复BM25索引 (doc_id={doc_id})。"
                "建议执行索引重建修复一致性。"
            )
            return

        try:
            rollback_ok = await self.bm25_retriever.update_document(
                doc_id, content, metadata or {}
            )
            if rollback_ok:
                logger.info(f"[回滚] 已恢复BM25索引 (doc_id={doc_id})")
            else:
                logger.error(
                    f"[回滚] BM25索引恢复失败 (doc_id={doc_id})，建议执行索引重建"
                )
        except Exception as e:
            logger.error(
                f"[回滚] BM25索引恢复异常 (doc_id={doc_id}): {e}",
                exc_info=True,
            )

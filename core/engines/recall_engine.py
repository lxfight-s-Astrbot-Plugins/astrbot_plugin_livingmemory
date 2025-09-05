# -*- coding: utf-8 -*-
"""
recall_engine.py - 回忆引擎
负责根据用户查询，使用多策略智能召回最相关的记忆。
支持密集向量检索、稀疏检索和混合检索。
"""

import json
import math
import time
from typing import List, Dict, Any, Optional

from astrbot.api import logger
from astrbot.api.star import Context
from ...storage.faiss_manager import FaissManager, Result
from ..retrieval import SparseRetriever, ResultFusion


class RecallEngine:
    """
    回忆引擎：负责根据用户查询，使用多策略智能召回最相关的记忆。
    支持密集向量检索、稀疏检索和混合检索。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        faiss_manager: FaissManager,
        sparse_retriever: Optional[SparseRetriever] = None,
    ):
        """
        初始化回忆引擎。

        Args:
            config (Dict[str, Any]): 插件配置中 'recall_engine' 部分的字典。
            faiss_manager (FaissManager): 数据库管理器实例。
            sparse_retriever (Optional[SparseRetriever]): 稀疏检索器实例。
        """
        self.config = config
        self.faiss_manager = faiss_manager
        self.sparse_retriever = sparse_retriever

        # 初始化结果融合器
        fusion_config = config.get("fusion", {})
        fusion_strategy = fusion_config.get("strategy", "rrf")
        self.result_fusion = ResultFusion(
            strategy=fusion_strategy, config=fusion_config
        )

        logger.info("RecallEngine 初始化成功。")

    async def recall(
        self,
        context: Context,
        query: str,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
        k: Optional[int] = None,
    ) -> List[Result]:
        """
        执行回忆流程，检索并可能重排记忆。

        Args:
            query (str): 用户查询文本。
            session_id (Optional[str], optional): 当前会话 ID. Defaults to None.
            persona_id (Optional[str], optional): 当前人格 ID. Defaults to None.
            k (Optional[int], optional): 希望返回的记忆数量，如果为 None 则从配置中读取.

        Returns:
            List[Result]: 最终返回给上层应用的记忆列表。
        """
        top_k = k if k is not None else self.config.get("top_k", 5)
        retrieval_mode = self.config.get(
            "retrieval_mode", "hybrid"
        )  # hybrid, dense, sparse

        logger.info(
            f"开始记忆召回 - 查询: '{query}', 返回数量: {top_k}, 检索模式: {retrieval_mode}"
        )
        logger.debug(f"过滤条件 - 会话ID: {session_id}, 人格ID: {persona_id}")

        # 分析查询特征（用于自适应策略）
        query_info = self.result_fusion.analyze_query(query)
        logger.debug(f"Query analysis: {query_info}")

        # 根据检索模式执行搜索
        if retrieval_mode == "hybrid" and self.sparse_retriever:
            # 混合检索
            logger.debug("使用混合检索模式...")
            return await self._hybrid_search(
                context, query, session_id, persona_id, top_k, query_info
            )
        elif retrieval_mode == "sparse" and self.sparse_retriever:
            # 纯稀疏检索
            logger.debug("使用稀疏检索模式...")
            return await self._sparse_search(query, session_id, persona_id, top_k)
        else:
            # 纯密集检索（默认）
            logger.debug("使用密集检索模式...")
            return await self._dense_search(
                context, query, session_id, persona_id, top_k
            )

    async def _hybrid_search(
        self,
        context: Context,
        query: str,
        session_id: Optional[str],
        persona_id: Optional[str],
        k: int,
        query_info: Dict[str, Any],
    ) -> List[Result]:
        """执行混合检索"""
        # 并行执行密集和稀疏检索
        import asyncio

        # 密集检索
        dense_task = self.faiss_manager.search_memory(
            query=query, k=k * 2, session_id=session_id, persona_id=persona_id
        )

        # 稀疏检索
        sparse_task = self.sparse_retriever.search(
            query=query, limit=k * 2, session_id=session_id, persona_id=persona_id
        )

        # 等待两个检索完成
        dense_results, sparse_results = await asyncio.gather(
            dense_task, sparse_task, return_exceptions=True
        )

        # 处理异常
        if isinstance(dense_results, Exception):
            logger.error(f"密集检索失败: {dense_results}")
            dense_results = []
        if isinstance(sparse_results, Exception):
            logger.error(f"稀疏检索失败: {sparse_results}")
            sparse_results = []

        logger.debug(
            f"Dense results: {len(dense_results)}, Sparse results: {len(sparse_results)}"
        )

        # 记录融合前的详细信息
        logger.debug(f"[{session_id}] 融合前详细信息:")
        logger.debug(f"[{session_id}] - 密集检索结果数量: {len(dense_results)}")
        logger.debug(f"[{session_id}] - 稀疏检索结果数量: {len(sparse_results)}")

        # 记录密集检索结果详情
        for i, result in enumerate(dense_results[:3]):  # 只记录前3个结果
            logger.debug(
                f"[{session_id}] 密集结果 {i + 1}: 相似度={result.similarity:.3f}, 内容预览={result.data.get('text', '')[:50]}..."
            )

        # 记录稀疏检索结果详情
        for i, result in enumerate(sparse_results[:3]):  # 只记录前3个结果
            logger.debug(
                f"[{session_id}] 稀疏结果 {i + 1}: 分数={result.score:.3f}, 内容预览={result.content[:50]}..."
            )

        # 融合结果
        logger.debug(f"[{session_id}] 开始结果融合...")
        fused_results = self.result_fusion.fuse(
            dense_results=dense_results,
            sparse_results=sparse_results,
            k=k,
            query_info=query_info,
        )
        logger.debug(f"[{session_id}] 融合完成，获得 {len(fused_results)} 个融合结果")

        # 转换回 Result 格式
        final_results = []
        for result in fused_results:
            final_results.append(
                Result(
                    data={
                        "id": result.doc_id,
                        "text": result.content,
                        "metadata": result.metadata,
                    },
                    similarity=result.final_score,
                )
            )

        # 记录融合后的结果详情
        logger.debug(f"[{session_id}] 融合后结果详情:")
        for i, result in enumerate(final_results[:3]):  # 只记录前3个结果
            logger.debug(
                f"[{session_id}] 融合结果 {i + 1}: 最终分={result.similarity:.3f}, 内容预览={result.data.get('text', '')[:50]}..."
            )

        # 应用传统的加权重排（如果需要）
        strategy = self.config.get("recall_strategy", "weighted")
        if strategy == "weighted":
            logger.debug(f"[{session_id}] 对混合检索结果应用加权重排...")
            final_results = self._rerank_by_weighted_score(context, final_results)

            # 记录重排后的结果
            logger.debug(f"[{session_id}] 重排后最终结果:")
            for i, result in enumerate(final_results[:3]):  # 只记录前3个结果
                logger.debug(
                    f"[{session_id}] 最终结果 {i + 1}: 加权分={result.similarity:.3f}, 内容预览={result.data.get('text', '')[:50]}..."
                )

        logger.info(
            f"[{session_id}] 🎯 混合检索完成，返回 {len(final_results)} 个记忆结果"
        )
        return final_results

    async def _dense_search(
        self,
        context: Context,
        query: str,
        session_id: Optional[str],
        persona_id: Optional[str],
        k: int,
    ) -> List[Result]:
        """执行密集检索"""
        logger.debug(
            f"[{session_id}] 开始密集检索，查询: '{query[:50]}...', 返回数量: {k}"
        )
        results = await self.faiss_manager.search_memory(
            query=query, k=k, session_id=session_id, persona_id=persona_id
        )

        if not results:
            logger.info(f"[{session_id}] 密集检索未找到相关记忆")
            return []

        logger.debug(f"[{session_id}] 密集检索找到 {len(results)} 个候选记忆")

        # 记录候选记忆详情
        for i, result in enumerate(results[:3]):  # 只记录前3个结果
            logger.debug(
                f"[{session_id}] 密集候选 {i + 1}: 相似度={result.similarity:.3f}, 内容预览={result.data.get('text', '')[:50]}..."
            )

        # 应用重排
        strategy = self.config.get("recall_strategy", "weighted")
        if strategy == "weighted":
            logger.debug(f"[{session_id}] 使用 'weighted' 策略进行重排...")
            reranked_results = self._rerank_by_weighted_score(context, results)

            # 记录重排后的结果
            logger.debug(f"[{session_id}] 密集检索重排后结果:")
            for i, result in enumerate(reranked_results[:3]):  # 只记录前3个结果
                logger.debug(
                    f"[{session_id}] 重排结果 {i + 1}: 加权分={result.similarity:.3f}, 内容预览={result.data.get('text', '')[:50]}..."
                )

            logger.info(
                f"[{session_id}] 🎯 密集检索完成，返回 {len(reranked_results)} 个记忆结果"
            )
            return reranked_results
        else:
            logger.debug(
                f"[{session_id}] 使用 'similarity' 策略，直接返回 {len(results)} 个结果"
            )
            logger.info(
                f"[{session_id}] 🎯 密集检索完成，返回 {len(results)} 个记忆结果"
            )
            return results

    async def _sparse_search(
        self, query: str, session_id: Optional[str], persona_id: Optional[str], k: int
    ) -> List[Result]:
        """执行稀疏检索"""
        logger.debug(
            f"[{session_id}] 开始稀疏检索，查询: '{query[:50]}...', 返回数量: {k}"
        )
        sparse_results = await self.sparse_retriever.search(
            query=query, limit=k, session_id=session_id, persona_id=persona_id
        )

        if not sparse_results:
            logger.info(f"[{session_id}] 稀疏检索未找到相关记忆")
            return []

        logger.debug(f"[{session_id}] 稀疏检索找到 {len(sparse_results)} 个候选记忆")

        # 记录稀疏检索候选记忆详情
        for i, result in enumerate(sparse_results[:3]):  # 只记录前3个结果
            logger.debug(
                f"[{session_id}] 稀疏候选 {i + 1}: 分数={result.score:.3f}, 内容预览={result.content[:50]}..."
            )

        # 转换为 Result 格式
        results = []
        for result in sparse_results:
            results.append(
                Result(
                    data={
                        "id": result.doc_id,
                        "text": result.content,
                        "metadata": result.metadata,
                    },
                    similarity=result.score,
                )
            )

        logger.info(f"[{session_id}] 🎯 稀疏检索完成，返回 {len(results)} 个记忆结果")
        return results

    def _rerank_by_weighted_score(
        self, context: Context, results: List[Result]
    ) -> List[Result]:
        """
        根据相似度、重要性和新近度对结果进行加权重排。
        """
        sim_w = self.config.get("similarity_weight", 0.6)
        imp_w = self.config.get("importance_weight", 0.2)
        rec_w = self.config.get("recency_weight", 0.2)

        reranked_results = []
        current_time = time.time()

        for res in results:
            # 安全解析元数据
            metadata = res.data.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError as e:
                    logger.warning(f"解析记忆元数据失败: {e}")
                    metadata = {}

            # 归一化各项得分 (0-1)
            similarity_score = res.similarity
            importance_score = metadata.get("importance", 0.0)

            # 计算新近度得分
            last_access = metadata.get("last_access_time", current_time)
            # 增加健壮性检查，以防 last_access 是字符串
            if isinstance(last_access, str):
                try:
                    last_access = float(last_access)
                except (ValueError, TypeError):
                    last_access = current_time

            hours_since_access = (current_time - last_access) / 3600
            # 使用指数衰减，半衰期约为24小时
            recency_score = math.exp(-0.028 * hours_since_access)

            # 计算最终加权分
            final_score = (
                similarity_score * sim_w
                + importance_score * imp_w
                + recency_score * rec_w
            )

            # 直接修改现有 Result 对象的 similarity 分数
            res.similarity = final_score
            reranked_results.append(res)

        # 按最终得分降序排序
        reranked_results.sort(key=lambda x: x.similarity, reverse=True)

        return reranked_results

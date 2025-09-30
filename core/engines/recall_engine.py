# -*- coding: utf-8 -*-
"""
recall_engine.py - 回忆引擎
负责根据用户查询，使用多策略智能召回最相关的记忆。
支持密集向量检索、稀疏检索和混合检索。
"""

import json
import math
from typing import List, Dict, Any, Optional

from astrbot.api import logger
from astrbot.api.star import Context
from ...storage.faiss_manager import FaissManager, Result
from ..retrieval import SparseRetriever, ResultFusion, SearchResult
from ..utils import get_now_datetime


class RecallEngine:
    """
    回忆引擎：负责根据用户查询，使用多策略智能召回最相关的记忆。
    支持密集向量检索、稀疏检索和混合检索。
    """

    def __init__(self, config: Dict[str, Any], faiss_manager: FaissManager, sparse_retriever: Optional[SparseRetriever] = None):
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
        self.result_fusion = ResultFusion(strategy=fusion_strategy, config=fusion_config)

        # 验证和归一化权重
        sim_w = self.config.get("similarity_weight", 0.5)
        imp_w = self.config.get("importance_weight", 0.3)
        rec_w = self.config.get("recency_weight", 0.2)

        total_weight = sim_w + imp_w + rec_w
        if abs(total_weight - 1.0) > 0.01:
            logger.warning(f"权重之和 {total_weight} 不等于1.0，将自动归一化")
            self.config["similarity_weight"] = sim_w / total_weight
            self.config["importance_weight"] = imp_w / total_weight
            self.config["recency_weight"] = rec_w / total_weight
            logger.info(
                f"归一化后的权重: similarity={self.config['similarity_weight']:.3f}, "
                f"importance={self.config['importance_weight']:.3f}, "
                f"recency={self.config['recency_weight']:.3f}"
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
        retrieval_mode = self.config.get("retrieval_mode", "hybrid")  # hybrid, dense, sparse

        # 分析查询特征（用于自适应策略）
        query_info = self.result_fusion.analyze_query(query)
        logger.debug(f"Query analysis: {query_info}")

        # 根据检索模式执行搜索
        if retrieval_mode == "hybrid" and self.sparse_retriever:
            # 混合检索
            logger.debug("使用混合检索模式...")
            return await self._hybrid_search(context, query, session_id, persona_id, top_k, query_info)
        elif retrieval_mode == "sparse" and self.sparse_retriever:
            # 纯稀疏检索
            logger.debug("使用稀疏检索模式...")
            return await self._sparse_search(query, session_id, persona_id, top_k)
        else:
            # 纯密集检索（默认）
            logger.debug("使用密集检索模式...")
            return await self._dense_search(context, query, session_id, persona_id, top_k)

    async def _hybrid_search(
        self,
        context: Context,
        query: str,
        session_id: Optional[str],
        persona_id: Optional[str],
        k: int,
        query_info: Dict[str, Any]
    ) -> List[Result]:
        """执行混合检索"""
        # 并行执行密集和稀疏检索
        import asyncio
        
        # 密集检索
        dense_task = self.faiss_manager.search_memory(
            query=query, k=k*2, session_id=session_id, persona_id=persona_id
        )
        
        # 稀疏检索
        sparse_task = self.sparse_retriever.search(
            query=query, limit=k*2, session_id=session_id, persona_id=persona_id
        )
        
        # 等待两个检索完成
        dense_results, sparse_results = await asyncio.gather(dense_task, sparse_task, return_exceptions=True)
        
        # 处理异常
        if isinstance(dense_results, Exception):
            logger.error(f"密集检索失败: {dense_results}")
            dense_results = []
        if isinstance(sparse_results, Exception):
            logger.error(f"稀疏检索失败: {sparse_results}")
            sparse_results = []
        
        logger.debug(f"Dense results: {len(dense_results)}, Sparse results: {len(sparse_results)}")
        
        # 融合结果
        fused_results = self.result_fusion.fuse(
            dense_results=dense_results,
            sparse_results=sparse_results,
            k=k,
            query_info=query_info
        )
        
        # 转换回 Result 格式
        final_results = []
        for result in fused_results:
            final_results.append(Result(
                data={
                    "id": result.doc_id,
                    "text": result.content,
                    "metadata": result.metadata
                },
                similarity=result.final_score
            ))
        
        # 应用传统的加权重排（如果需要）
        strategy = self.config.get("recall_strategy", "weighted")
        if strategy == "weighted":
            logger.debug("对混合检索结果应用加权重排...")
            final_results = self._rerank_by_weighted_score(context, final_results)
        
        return final_results

    async def _dense_search(
        self,
        context: Context,
        query: str,
        session_id: Optional[str],
        persona_id: Optional[str],
        k: int
    ) -> List[Result]:
        """执行密集检索"""
        results = await self.faiss_manager.search_memory(
            query=query, k=k, session_id=session_id, persona_id=persona_id
        )
        
        if not results:
            return []
        
        # 应用重排
        strategy = self.config.get("recall_strategy", "weighted")
        if strategy == "weighted":
            logger.debug("使用 'weighted' 策略进行重排...")
            return self._rerank_by_weighted_score(context, results)
        else:
            logger.debug("使用 'similarity' 策略，直接返回结果。")
            return results

    async def _sparse_search(
        self,
        query: str,
        session_id: Optional[str],
        persona_id: Optional[str],
        k: int
    ) -> List[Result]:
        """执行稀疏检索"""
        sparse_results = await self.sparse_retriever.search(
            query=query, limit=k, session_id=session_id, persona_id=persona_id
        )
        
        # 转换为 Result 格式
        results = []
        for result in sparse_results:
            results.append(Result(
                data={
                    "id": result.doc_id,
                    "text": result.content,
                    "metadata": result.metadata
                },
                similarity=result.score
            ))
        
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
        current_time = get_now_datetime(context).timestamp()

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
            from ..utils import validate_timestamp
            last_access = validate_timestamp(metadata.get("last_access_time"), current_time)

            hours_since_access = max(0, (current_time - last_access) / 3600)  # 确保非负
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

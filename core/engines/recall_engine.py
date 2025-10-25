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

        # 记录配置信息
        retrieval_mode = config.get("retrieval_mode", "hybrid")
        top_k = config.get("top_k", 5)
        logger.info(f"RecallEngine 初始化成功")
        logger.info(f"  检索模式: {retrieval_mode}")
        logger.info(f"  默认返回数量: {top_k}")
        logger.info(f"  融合策略: {fusion_strategy}")
        logger.info(f"  稀疏检索器: {'已启用' if sparse_retriever else '未启用'}")
    def _is_dense_available(self) -> bool:
        """
        检查密集检索是否可用
        
        Returns:
            bool: 密集检索是否可用
        """
        return self.faiss_manager is not None and self.faiss_manager.index.ntotal > 0

    def _is_sparse_available(self) -> bool:
        """
        检查稀疏检索是否可用
        
        Returns:
            bool: 稀疏检索是否可用
        """
        return self.sparse_retriever is not None


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

        logger.info(f"🔍 开始召回记忆")
        logger.debug(f"  查询内容: {query[:100]}{'...' if len(query) > 100 else ''}")
        logger.debug(f"  检索模式: {retrieval_mode}")
        logger.debug(f"  目标数量: {top_k}")
        logger.debug(f"  会话ID: {session_id or '无'}")
        logger.debug(f"  人格ID: {persona_id or '无'}")

        # 分析查询特征（用于自适应策略）
        query_info = self.result_fusion.analyze_query(query)
        logger.debug(f"  查询分析: 长度={query_info.get('length', 0)}, 关键词数={len(query_info.get('keywords', []))}")

        try:
            # 根据检索模式执行搜索
            if retrieval_mode == "hybrid" and self.sparse_retriever:
                # 混合检索
                logger.info("📊 使用混合检索模式 (密集向量 + 稀疏关键词)")
                results = await self._hybrid_search(context, query, session_id, persona_id, top_k, query_info)
            elif retrieval_mode == "sparse" and self.sparse_retriever:
                # 纯稀疏检索
                logger.info("🔤 使用稀疏检索模式 (BM25)")
                results = await self._sparse_search(query, session_id, persona_id, top_k)
            else:
                # 纯密集检索（默认）
                logger.info("🎯 使用密集检索模式 (向量相似度)")
                results = await self._dense_search(context, query, session_id, persona_id, top_k)

            logger.info(f"✅ 召回完成，返回 {len(results)} 条记忆")
            if results:
                logger.debug(f"  最高相似度: {results[0].similarity:.3f}")
                logger.debug(f"  最低相似度: {results[-1].similarity:.3f}")

            return results

        except Exception as e:
            logger.error(f"❌ 召回记忆时发生错误: {type(e).__name__}: {e}", exc_info=True)
            logger.error(f"  错误上下文: query='{query[:50]}...', mode={retrieval_mode}, k={top_k}")
            return []

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
        logger.debug(f"混合检索: 目标数量={k}, 每路检索={k*2}")

        # 并行执行密集和稀疏检索
        import asyncio

        try:
            # 密集检索
            logger.debug("  启动密集检索任务...")
            dense_task = self.faiss_manager.search_memory(
                query=query, k=k*2, session_id=session_id, persona_id=persona_id
            )

            # 稀疏检索
            logger.debug("  启动稀疏检索任务...")
            sparse_task = self.sparse_retriever.search(
                query=query, limit=k*2, session_id=session_id, persona_id=persona_id
            )

            # 等待两个检索完成
            dense_results, sparse_results = await asyncio.gather(dense_task, sparse_task, return_exceptions=True)

            # 处理异常并实现自动退化机制
            if isinstance(dense_results, Exception):
                logger.error(f"❌ 密集检索失败: {type(dense_results).__name__}: {dense_results}")
                dense_results = []
            else:
                logger.debug(f"  密集检索返回: {len(dense_results)} 条结果")

            if isinstance(sparse_results, Exception):
                logger.error(f"❌ 稀疏检索失败: {type(sparse_results).__name__}: {sparse_results}")
                sparse_results = []
            else:
                logger.debug(f"  稀疏检索返回: {len(sparse_results)} 条结果")

            # 自动退化机制：当某一检索器不可用时，自动降级到另一种检索模式
            if not dense_results and not sparse_results:
                logger.warning("⚠️ 混合检索两路均无结果")
                return []
            elif not dense_results and sparse_results:
                logger.warning(f"⚠️ 密集检索不可用，自动退化为纯稀疏检索")
                return await self._sparse_search(query, session_id, persona_id, k)
            elif dense_results and not sparse_results:
                logger.warning(f"⚠️ 稀疏检索不可用，自动退化为纯密集检索")
                return await self._dense_search(context, query, session_id, persona_id, k)

            # 融合结果
            logger.debug(f"  开始融合结果，策略: {self.result_fusion.strategy}")
            fused_results = self.result_fusion.fuse(
                dense_results=dense_results,
                sparse_results=sparse_results,
                k=k,
                query_info=query_info
            )
            logger.debug(f"  融合完成，返回 {len(fused_results)} 条结果")

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
                logger.debug("  应用加权重排 (相似度+重要性+新近度)...")
                final_results = self._rerank_by_weighted_score(context, final_results)
                logger.debug(f"  重排完成，最终返回 {len(final_results)} 条结果")

            return final_results

        except Exception as e:
            logger.error(f"❌ 混合检索过程发生异常: {type(e).__name__}: {e}", exc_info=True)
            return []

    async def _dense_search(
        self,
        context: Context,
        query: str,
        session_id: Optional[str],
        persona_id: Optional[str],
        k: int
    ) -> List[Result]:
        """执行密集检索"""
        logger.debug(f"密集检索: k={k}")

        try:
            results = await self.faiss_manager.search_memory(
                query=query, k=k, session_id=session_id, persona_id=persona_id
            )

            if not results:
                logger.debug("  密集检索无结果")
                return []

            logger.debug(f"  密集检索返回 {len(results)} 条结果")

            # 应用重排
            strategy = self.config.get("recall_strategy", "weighted")
            if strategy == "weighted":
                logger.debug("  应用加权重排 (相似度+重要性+新近度)...")
                reranked = self._rerank_by_weighted_score(context, results)
                logger.debug(f"  重排完成，返回 {len(reranked)} 条结果")
                return reranked
            else:
                logger.debug(f"  使用 '{strategy}' 策略，直接返回原始结果")
                return results

        except Exception as e:
            logger.error(f"❌ 密集检索过程发生异常: {type(e).__name__}: {e}", exc_info=True)
            return []

    async def _sparse_search(
        self,
        query: str,
        session_id: Optional[str],
        persona_id: Optional[str],
        k: int
    ) -> List[Result]:
        """执行稀疏检索"""
        logger.debug(f"稀疏检索: k={k}")

        try:
            sparse_results = await self.sparse_retriever.search(
                query=query, limit=k, session_id=session_id, persona_id=persona_id
            )

            if not sparse_results:
                logger.debug("  稀疏检索无结果")
                return []

            logger.debug(f"  稀疏检索返回 {len(sparse_results)} 条结果")

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

        except Exception as e:
            logger.error(f"❌ 稀疏检索过程发生异常: {type(e).__name__}: {e}", exc_info=True)
            return []

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

# -*- coding: utf-8 -*-
"""
结果融合器 - 实现多种检索结果的融合策略
"""

import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

from astrbot.api import logger

try:
    from astrbot.core.db.vec_db.faiss_impl.vec_db import Result
except ImportError:
    # 定义 Result 类型
    @dataclass
    class Result:
        data: Dict[str, Any]
        similarity: float


@dataclass
class SearchResult:
    """统一搜索结果"""
    doc_id: int
    content: str
    metadata: Dict[str, Any]
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    final_score: float = 0.0


class ResultFusion:
    """结果融合器"""
    
    def __init__(self, strategy: str = "rrf", config: Dict[str, Any] = None):
        self.strategy = strategy
        self.config = config or {}
        
        # RRF 参数
        self.rrf_k = self.config.get("rrf_k", 60)
        
        # 加权融合参数
        self.dense_weight = self.config.get("dense_weight", 0.7)
        self.sparse_weight = self.config.get("sparse_weight", 0.3)
        
        # 分数归一化参数
        self.sparse_alpha = self.config.get("sparse_alpha", 1.0)  # BM25 分数缩放
        self.sparse_epsilon = self.config.get("sparse_epsilon", 0.0)  # 最小分数偏移
        
        # 新增参数
        self.convex_lambda = self.config.get("convex_lambda", 0.5)  # Convex Combination 参数
        self.interleave_ratio = self.config.get("interleave_ratio", 0.5)  # 交替融合比例
        self.rank_bias_factor = self.config.get("rank_bias_factor", 0.1)  # 排序偏置因子
        
    def fuse(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int = 10,
        query_info: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """融合检索结果"""
        if self.strategy == "rrf":
            return self._rrf_fusion(dense_results, sparse_results, k)
        elif self.strategy == "weighted":
            return self._weighted_fusion(dense_results, sparse_results, k)
        elif self.strategy == "cascade":
            return self._cascade_fusion(dense_results, sparse_results, k)
        elif self.strategy == "adaptive":
            return self._adaptive_fusion(dense_results, sparse_results, k, query_info)
        elif self.strategy == "convex":
            return self._convex_combination(dense_results, sparse_results, k)
        elif self.strategy == "interleave":
            return self._interleave_fusion(dense_results, sparse_results, k)
        elif self.strategy == "rank_fusion":
            return self._rank_fusion(dense_results, sparse_results, k)
        elif self.strategy == "score_fusion":
            return self._score_fusion(dense_results, sparse_results, k)
        elif self.strategy == "hybrid_rrf":
            return self._hybrid_rrf_fusion(dense_results, sparse_results, k, query_info)
        else:
            raise ValueError(f"Unknown fusion strategy: {self.strategy}")
    
    def _rrf_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """Reciprocal Rank Fusion (RRF)"""
        # 计算每个结果的 RRF 分数
        rrf_scores = defaultdict(float)
        result_map = {}
        
        # 处理密集检索结果
        for rank, result in enumerate(dense_results):
            doc_id = result.data["id"]
            rrf_scores[doc_id] += 1.0 / (self.rrf_k + rank + 1)
            result_map[doc_id] = result
        
        # 处理稀疏检索结果
        for rank, result in enumerate(sparse_results):
            doc_id = result.doc_id
            rrf_scores[doc_id] += 1.0 / (self.rrf_k + rank + 1)
            if doc_id not in result_map:
                result_map[doc_id] = result
        
        # 排序并返回前 k 个
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        final_results = []
        for doc_id, rrf_score in sorted_results[:k]:
            result = result_map[doc_id]
            
            if isinstance(result, Result):
                final_result = SearchResult(
                    doc_id=doc_id,
                    content=result.data["text"],
                    metadata=result.data.get("metadata", {}),
                    dense_score=result.similarity,
                    final_score=rrf_score
                )
            else:
                final_result = SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    sparse_score=result.score,
                    final_score=rrf_score
                )
            
            final_results.append(final_result)
        
        return final_results
    
    def _weighted_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """加权融合"""
        # 归一化分数,防止除零
        dense_scores = [r.similarity for r in dense_results]
        sparse_scores = [r.score for r in sparse_results]

        dense_max = max(dense_scores) if dense_scores else 1.0
        sparse_max = max(sparse_scores) if sparse_scores else 1.0

        # 防止除零
        if dense_max == 0:
            dense_max = 1.0
        if sparse_max == 0:
            sparse_max = 1.0
        
        # 合并结果
        result_map = {}
        
        # 处理密集结果
        for result in dense_results:
            doc_id = result.data["id"]
            result_map[doc_id] = SearchResult(
                doc_id=doc_id,
                content=result.data["text"],
                metadata=result.data.get("metadata", {}),
                dense_score=result.similarity / dense_max,
                final_score=0.0
            )
        
        # 处理稀疏结果并融合
        for result in sparse_results:
            doc_id = result.doc_id
            sparse_norm = result.score / sparse_max if sparse_max > 0 else 0
            
            if doc_id in result_map:
                # 已有密集结果，进行加权
                existing = result_map[doc_id]
                existing.sparse_score = sparse_norm
                existing.final_score = (
                    existing.dense_score * self.dense_weight +
                    sparse_norm * self.sparse_weight
                )
            else:
                # 只有稀疏结果
                result_map[doc_id] = SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    sparse_score=sparse_norm,
                    final_score=sparse_norm * self.sparse_weight
                )
        
        # 排序并返回
        sorted_results = sorted(
            result_map.values(),
            key=lambda x: x.final_score,
            reverse=True
        )
        
        return sorted_results[:k]
    
    def _cascade_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """级联融合：先用稀疏检索初筛，再用密集向量精排"""
        if not sparse_results:
            # 没有稀疏结果，直接返回密集结果
            return self._dense_to_search_results(dense_results, k)
        
        # 取稀疏检索的前 2*k 个结果
        sparse_candidates = sparse_results[:k*2]
        candidate_ids = {r.doc_id for r in sparse_candidates}
        
        # 从密集结果中筛选候选
        dense_candidates = [
            r for r in dense_results 
            if r.data["id"] in candidate_ids
        ]
        
        # 如果密集结果不足，用稀疏结果补充
        if len(dense_candidates) < k:
            candidate_ids.update(r.data["id"] for r in dense_candidates)
            additional_sparse = [
                r for r in sparse_results 
                if r.doc_id not in candidate_ids
            ][:k - len(dense_candidates)]
            
            # 合并结果
            all_results = self._dense_to_search_results(dense_candidates, k)
            for sparse in additional_sparse:
                all_results.append(SearchResult(
                    doc_id=sparse.doc_id,
                    content=sparse.content,
                    metadata=sparse.metadata,
                    sparse_score=sparse.score,
                    final_score=sparse.score
                ))
            
            # 按分数排序
            all_results.sort(key=lambda x: x.final_score, reverse=True)
            return all_results[:k]
        else:
            # 直接使用密集结果
            return self._dense_to_search_results(dense_candidates, k)
    
    def _adaptive_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int,
        query_info: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """自适应融合:根据查询特征选择融合策略(不修改实例变量)"""
        if not query_info:
            # 默认使用 RRF
            return self._rrf_fusion(dense_results, sparse_results, k)

        query_type = query_info.get("type", "mixed")
        query_length = query_info.get("length", 0)

        # 根据查询类型确定权重(使用局部变量)
        if query_type == "keyword" or query_length < 10:
            # 短查询或关键词查询,偏向稀疏检索
            sparse_w = 0.7
            dense_w = 0.3
        elif query_type == "semantic" or query_length > 50:
            # 长查询或语义查询,偏向密集检索
            sparse_w = 0.2
            dense_w = 0.8
        else:
            # 混合查询,使用 RRF
            return self._rrf_fusion(dense_results, sparse_results, k)

        # 使用临时权重进行加权融合
        return self._weighted_fusion_with_custom_weights(
            dense_results, sparse_results, k, dense_w, sparse_w
        )

    def _weighted_fusion_with_custom_weights(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int,
        dense_weight: float,
        sparse_weight: float
    ) -> List[SearchResult]:
        """使用自定义权重的加权融合(不修改实例变量)"""
        # 归一化分数
        dense_max = max(r.similarity for r in dense_results) if dense_results else 1.0
        sparse_max = max(r.score for r in sparse_results) if sparse_results else 1.0

        # 合并结果
        result_map = {}

        # 处理密集结果
        for result in dense_results:
            doc_id = result.data["id"]
            result_map[doc_id] = SearchResult(
                doc_id=doc_id,
                content=result.data["text"],
                metadata=result.data.get("metadata", {}),
                dense_score=result.similarity / dense_max,
                final_score=0.0
            )

        # 处理稀疏结果并融合
        for result in sparse_results:
            doc_id = result.doc_id
            sparse_norm = result.score / sparse_max if sparse_max > 0 else 0

            if doc_id in result_map:
                # 已有密集结果,进行加权
                existing = result_map[doc_id]
                existing.sparse_score = sparse_norm
                existing.final_score = (
                    existing.dense_score * dense_weight +
                    sparse_norm * sparse_weight
                )
            else:
                # 只有稀疏结果
                result_map[doc_id] = SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    sparse_score=sparse_norm,
                    final_score=sparse_norm * sparse_weight
                )

        # 排序并返回
        sorted_results = sorted(
            result_map.values(),
            key=lambda x: x.final_score,
            reverse=True
        )

        return sorted_results[:k]
    
    def _dense_to_search_results(self, dense_results: List[Result], k: int) -> List[SearchResult]:
        """将密集结果转换为 SearchResult"""
        results = []
        for result in dense_results[:k]:
            results.append(SearchResult(
                doc_id=result.data["id"],
                content=result.data["text"],
                metadata=result.data.get("metadata", {}),
                dense_score=result.similarity,
                final_score=result.similarity
            ))
        return results
    
    def analyze_query(self, query: str) -> Dict[str, Any]:
        """分析查询特征"""
        # 简单的查询分析
        query_lower = query.lower()
        
        # 检查是否为关键词查询
        keyword_indicators = ["是", "什么", "哪里", "谁", "什么时候", "how", "what", "where", "when", "who"]
        is_keyword = any(indicator in query_lower for indicator in keyword_indicators)
        
        # 检查是否包含实体
        entity_indicators = [":", "：", "的", "'s"]
        has_entities = any(indicator in query for indicator in entity_indicators)
        
        # 确定查询类型
        if is_keyword and len(query.split()) <= 5:
            query_type = "keyword"
        elif has_entities or len(query) > 100:
            query_type = "semantic"
        else:
            query_type = "mixed"
        
        return {
            "type": query_type,
            "length": len(query),
            "word_count": len(query.split()),
            "is_keyword": is_keyword,
            "has_entities": has_entities
        }
    
    def _convex_combination(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """凸组合融合 - 基于查询特征动态调整权重"""
        # 归一化分数到 [0,1] 区间
        dense_scores = self._normalize_scores([r.similarity for r in dense_results])
        sparse_scores = self._normalize_scores([r.score for r in sparse_results])
        
        result_map = {}
        
        # 处理密集结果
        for i, result in enumerate(dense_results):
            doc_id = result.data["id"]
            result_map[doc_id] = SearchResult(
                doc_id=doc_id,
                content=result.data["text"],
                metadata=result.data.get("metadata", {}),
                dense_score=dense_scores[i],
                final_score=0.0
            )
        
        # 处理稀疏结果
        sparse_map = {}
        for i, result in enumerate(sparse_results):
            sparse_map[result.doc_id] = sparse_scores[i]
        
        # 计算融合分数
        for doc_id, search_result in result_map.items():
            dense_score = search_result.dense_score
            sparse_score = sparse_map.get(doc_id, 0.0)
            
            # 凸组合：λ * dense + (1-λ) * sparse
            search_result.sparse_score = sparse_score
            search_result.final_score = (
                self.convex_lambda * dense_score + 
                (1 - self.convex_lambda) * sparse_score
            )
        
        # 处理只有稀疏结果的文档
        for doc_id, sparse_score in sparse_map.items():
            if doc_id not in result_map:
                sparse_result = next(r for r in sparse_results if r.doc_id == doc_id)
                result_map[doc_id] = SearchResult(
                    doc_id=doc_id,
                    content=sparse_result.content,
                    metadata=sparse_result.metadata,
                    sparse_score=sparse_score,
                    final_score=(1 - self.convex_lambda) * sparse_score
                )
        
        # 排序并返回
        sorted_results = sorted(
            result_map.values(), 
            key=lambda x: x.final_score, 
            reverse=True
        )
        return sorted_results[:k]
    
    def _interleave_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """交替融合 - 按比例交替选择不同检索器的结果"""
        dense_idx, sparse_idx = 0, 0
        final_results = []
        seen_docs = set()
        
        dense_count = int(k * self.interleave_ratio)
        sparse_count = k - dense_count
        
        # 交替选择结果
        while len(final_results) < k and (dense_idx < len(dense_results) or sparse_idx < len(sparse_results)):
            # 优先从密集结果中选择
            if len([r for r in final_results if r.dense_score is not None]) < dense_count:
                if dense_idx < len(dense_results):
                    result = dense_results[dense_idx]
                    doc_id = result.data["id"]
                    if doc_id not in seen_docs:
                        final_results.append(SearchResult(
                            doc_id=doc_id,
                            content=result.data["text"],
                            metadata=result.data.get("metadata", {}),
                            dense_score=result.similarity,
                            final_score=result.similarity
                        ))
                        seen_docs.add(doc_id)
                    dense_idx += 1
                    continue
            
            # 从稀疏结果中选择
            if sparse_idx < len(sparse_results):
                result = sparse_results[sparse_idx]
                doc_id = result.doc_id
                if doc_id not in seen_docs:
                    final_results.append(SearchResult(
                        doc_id=doc_id,
                        content=result.content,
                        metadata=result.metadata,
                        sparse_score=result.score,
                        final_score=result.score
                    ))
                    seen_docs.add(doc_id)
                sparse_idx += 1
        
        return final_results
    
    def _rank_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """基于排序的融合 - 考虑文档在不同排序列表中的位置"""
        result_scores = defaultdict(lambda: {"dense_rank": float('inf'), "sparse_rank": float('inf'), "result": None})
        
        # 记录密集结果的排序
        for rank, result in enumerate(dense_results):
            doc_id = result.data["id"]
            result_scores[doc_id]["dense_rank"] = rank + 1
            result_scores[doc_id]["result"] = result
        
        # 记录稀疏结果的排序  
        for rank, result in enumerate(sparse_results):
            doc_id = result.doc_id
            result_scores[doc_id]["sparse_rank"] = rank + 1
            if result_scores[doc_id]["result"] is None:
                result_scores[doc_id]["result"] = result
        
        # 计算基于排序的融合分数
        fusion_results = []
        for doc_id, scores in result_scores.items():
            dense_rank = scores["dense_rank"]
            sparse_rank = scores["sparse_rank"]
            
            # 使用排序倒数的加权和
            rank_score = 0
            if dense_rank != float('inf'):
                rank_score += self.dense_weight / dense_rank
            if sparse_rank != float('inf'):
                rank_score += self.sparse_weight / sparse_rank
            
            # 添加排序偏置
            if dense_rank != float('inf') and sparse_rank != float('inf'):
                # 在两个列表中都出现，给额外加分
                rank_score += self.rank_bias_factor
            
            result = scores["result"]
            if isinstance(result, Result):
                fusion_results.append(SearchResult(
                    doc_id=doc_id,
                    content=result.data["text"],
                    metadata=result.data.get("metadata", {}),
                    dense_score=result.similarity if dense_rank != float('inf') else None,
                    sparse_score=getattr(result, 'score', None) if sparse_rank != float('inf') else None,
                    final_score=rank_score
                ))
            else:
                fusion_results.append(SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    dense_score=None,
                    sparse_score=result.score if sparse_rank != float('inf') else None,
                    final_score=rank_score
                ))
        
        # 排序并返回
        fusion_results.sort(key=lambda x: x.final_score, reverse=True)
        return fusion_results[:k]
    
    def _score_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """基于分数的高级融合 - 使用Borda Count和CombSUM结合"""
        # 创建文档到分数的映射
        dense_scores = {r.data["id"]: r.similarity for r in dense_results}
        sparse_scores = {r.doc_id: r.score for r in sparse_results}
        
        all_doc_ids = set(dense_scores.keys()) | set(sparse_scores.keys())
        
        # 计算 Borda Count
        borda_scores = {}
        for doc_id in all_doc_ids:
            borda_score = 0
            
            # 密集检索的Borda分数
            if doc_id in dense_scores:
                dense_rank = sum(1 for other_score in dense_scores.values() 
                               if other_score > dense_scores[doc_id])
                borda_score += (len(dense_results) - dense_rank) * self.dense_weight
            
            # 稀疏检索的Borda分数
            if doc_id in sparse_scores:
                sparse_rank = sum(1 for other_score in sparse_scores.values() 
                                if other_score > sparse_scores[doc_id])
                borda_score += (len(sparse_results) - sparse_rank) * self.sparse_weight
            
            borda_scores[doc_id] = borda_score
        
        # 转换为SearchResult
        fusion_results = []
        result_map = {}
        
        # 建立结果映射
        for result in dense_results:
            result_map[result.data["id"]] = result
        for result in sparse_results:
            if result.doc_id not in result_map:
                result_map[result.doc_id] = result
        
        for doc_id in all_doc_ids:
            result = result_map[doc_id]
            
            if isinstance(result, Result):
                fusion_results.append(SearchResult(
                    doc_id=doc_id,
                    content=result.data["text"],
                    metadata=result.data.get("metadata", {}),
                    dense_score=dense_scores.get(doc_id),
                    sparse_score=sparse_scores.get(doc_id),
                    final_score=borda_scores[doc_id]
                ))
            else:
                fusion_results.append(SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    dense_score=dense_scores.get(doc_id),
                    sparse_score=sparse_scores.get(doc_id),
                    final_score=borda_scores[doc_id]
                ))
        
        fusion_results.sort(key=lambda x: x.final_score, reverse=True)
        return fusion_results[:k]
    
    def _hybrid_rrf_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int,
        query_info: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """混合RRF融合 - 根据查询特征动态调整RRF参数"""
        # 根据查询信息调整RRF参数
        dynamic_rrf_k = self.rrf_k
        
        if query_info:
            query_type = query_info.get("type", "mixed")
            query_length = query_info.get("length", 0)
            
            # 短查询或关键词查询，降低RRF_k以增强稀疏检索的影响
            if query_type == "keyword" or query_length < 20:
                dynamic_rrf_k = max(30, self.rrf_k * 0.5)
            # 长查询或语义查询，提高RRF_k以增强密集检索的影响  
            elif query_type == "semantic" or query_length > 100:
                dynamic_rrf_k = min(120, self.rrf_k * 1.5)
        
        # 使用动态RRF参数进行融合
        rrf_scores = defaultdict(float)
        result_map = {}
        
        # 处理密集检索结果
        for rank, result in enumerate(dense_results):
            doc_id = result.data["id"]
            rrf_scores[doc_id] += 1.0 / (dynamic_rrf_k + rank + 1)
            result_map[doc_id] = result
        
        # 处理稀疏检索结果
        for rank, result in enumerate(sparse_results):
            doc_id = result.doc_id
            rrf_scores[doc_id] += 1.0 / (dynamic_rrf_k + rank + 1)
            if doc_id not in result_map:
                result_map[doc_id] = result
        
        # 添加多样性奖励
        diversity_bonus = self.config.get("diversity_bonus", 0.1)
        if diversity_bonus > 0:
            self._apply_diversity_bonus(rrf_scores, result_map, diversity_bonus)
        
        # 排序并返回前 k 个
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        final_results = []
        for doc_id, rrf_score in sorted_results[:k]:
            result = result_map[doc_id]
            
            if isinstance(result, Result):
                final_result = SearchResult(
                    doc_id=doc_id,
                    content=result.data["text"],
                    metadata=result.data.get("metadata", {}),
                    dense_score=result.similarity,
                    final_score=rrf_score
                )
            else:
                final_result = SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    sparse_score=result.score,
                    final_score=rrf_score
                )
            
            final_results.append(final_result)
        
        return final_results
    
    def _normalize_scores(self, scores: List[float]) -> List[float]:
        """Min-Max 归一化"""
        import math

        if not scores:
            return []

        # 过滤无效值
        valid_scores = [s for s in scores if not (math.isnan(s) or math.isinf(s))]
        if not valid_scores:
            logger.warning("所有分数无效,使用默认值")
            return [0.5] * len(scores)

        min_score = min(valid_scores)
        max_score = max(valid_scores)

        if max_score == min_score:
            return [0.5] * len(scores)

        # 归一化,无效值映射到0
        normalized = []
        for score in scores:
            if math.isnan(score) or math.isinf(score):
                normalized.append(0.0)
            else:
                normalized.append((score - min_score) / (max_score - min_score))

        return normalized
    
    def _apply_diversity_bonus(
        self, 
        scores: Dict[int, float], 
        result_map: Dict[int, Any], 
        bonus: float
    ):
        """应用多样性奖励，避免结果过于相似"""
        # 简单的多样性策略：基于内容长度差异
        contents = {}
        for doc_id, result in result_map.items():
            if isinstance(result, Result):
                contents[doc_id] = len(result.data["text"])
            else:
                contents[doc_id] = len(result.content)
        
        # 给内容长度差异较大的文档额外加分
        content_lengths = list(contents.values())
        if content_lengths:
            avg_length = sum(content_lengths) / len(content_lengths)
            for doc_id, length in contents.items():
                diversity_factor = abs(length - avg_length) / avg_length if avg_length > 0 else 0
                scores[doc_id] += bonus * min(diversity_factor, 1.0)
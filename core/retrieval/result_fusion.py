# -*- coding: utf-8 -*-
"""
结果融合器 - 使用RRF (Reciprocal Rank Fusion) 策略
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
    """结果融合器 - 使用RRF (Reciprocal Rank Fusion) 策略"""
    
    def __init__(self, strategy: str = "rrf", config: Dict[str, Any] = None):
        self.strategy = "rrf"  # 固定为RRF策略
        self.config = config or {}
        
        # RRF 参数
        self.rrf_k = self.config.get("rrf_k", 60)
        
    def fuse(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int = 10,
        query_info: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """融合检索结果 - 使用RRF策略"""
        return self._rrf_fusion(dense_results, sparse_results, k)
    
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
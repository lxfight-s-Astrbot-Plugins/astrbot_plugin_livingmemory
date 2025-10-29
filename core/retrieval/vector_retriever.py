# -*- coding: utf-8 -*-
"""
向量检索器 - 基于Faiss的向量密集检索
封装AstrBot的FaissVecDB,提供统一的检索接口
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
from ..text_processor import TextProcessor


@dataclass
class VectorResult:
    """向量检索结果"""

    doc_id: int
    score: float
    content: str
    metadata: Dict[str, Any]


class VectorRetriever:
    """
    向量密集检索器

    封装AstrBot的FaissVecDB,提供统一的向量相似度检索接口。
    主要特性:
    1. 支持可选的查询预处理(使用TextProcessor去除停用词)
    2. 元数据包含:importance, create_time, last_access_time, session_id, persona_id
    3. 相似度分数已归一化到[0,1]区间
    4. 支持通过metadata过滤session_id和persona_id
    """

    def __init__(
        self,
        faiss_db: FaissVecDB,
        text_processor: Optional[TextProcessor] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化向量检索器

        Args:
            faiss_db: FaissVecDB实例
            text_processor: 文本处理器实例(可选,用于查询预处理)
            config: 配置字典(可选)
        """
        self.faiss_db = faiss_db
        self.text_processor = text_processor
        self.config = config or {}

        # 是否启用查询预处理
        self.enable_query_preprocessing = self.config.get(
            "enable_query_preprocessing", False
        )

    async def add_document(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        添加文档到向量库

        Args:
            content: 文档内容
            metadata: 文档元数据(必须包含:importance, create_time, last_access_time,
                     session_id, persona_id)

        Returns:
            int: 文档ID
        """
        # 确保metadata存在
        metadata = metadata or {}

        # 验证必需的元数据字段
        required_fields = [
            "importance",
            "create_time",
            "last_access_time",
            "session_id",
            "persona_id",
        ]
        for field in required_fields:
            if field not in metadata:
                # 提供默认值
                if field == "importance":
                    metadata[field] = 0.5
                elif field in ["create_time", "last_access_time"]:
                    import time

                    metadata[field] = time.time()
                else:  # session_id, persona_id
                    metadata[field] = None

        # 插入到Faiss向量库
        doc_id = await self.faiss_db.insert(content=content, metadata=metadata)

        return doc_id

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[VectorResult]:
        """
        执行向量相似度搜索

        Args:
            query: 查询字符串
            k: 返回的结果数量
            session_id: 会话ID过滤(可选)
            persona_id: 人格ID过滤(可选)

        Returns:
            List[VectorResult]: 向量检索结果,按相似度降序排列
        """
        if not query or not query.strip():
            return []

        # 可选的查询预处理
        processed_query = query
        if self.enable_query_preprocessing and self.text_processor:
            tokens = self.text_processor.tokenize(query, remove_stopwords=True)
            if tokens:
                processed_query = " ".join(tokens)
            else:
                # 如果预处理后为空,使用原始查询
                processed_query = query

        # 构建元数据过滤器 - session_id和persona_id已经被_extract_session_uuid处理
        metadata_filters = {}
        if session_id is not None:
            metadata_filters["session_id"] = session_id
        if persona_id is not None:
            metadata_filters["persona_id"] = persona_id

        # 执行向量检索
        # fetch_k设置为k*2以确保过滤后有足够的结果
        fetch_k = k * 2 if metadata_filters else k

        faiss_results = await self.faiss_db.retrieve(
            query=processed_query,
            k=k,
            fetch_k=fetch_k,
            rerank=False,
            metadata_filters=metadata_filters if metadata_filters else None,
        )

        # 转换为VectorResult格式
        results = []
        for result in faiss_results:
            # FaissVecDB返回的Result对象包含similarity和data
            # data是包含id, text, metadata的字典
            doc_data = result.data
            results.append(
                VectorResult(
                    doc_id=doc_data["id"],
                    score=result.similarity,  # FaissVecDB已经归一化到[0,1]
                    content=doc_data["text"],
                    metadata=doc_data["metadata"],
                )
            )

        return results

    async def update_metadata(self, doc_id: int, metadata: Dict[str, Any]) -> bool:
        """
        更新文档元数据

        注意: FaissVecDB不直接支持元数据更新,需要通过DocumentStorage实现

        Args:
            doc_id: 文档ID
            metadata: 新的元数据

        Returns:
            bool: 是否成功更新
        """
        try:
            # 访问FaissVecDB的document_storage来更新元数据
            doc_storage = self.faiss_db.document_storage

            # 先获取文档以验证存在
            doc = await doc_storage.get_document(doc_id)
            if not doc:
                return False

            # 更新元数据
            await doc_storage.update_metadata(doc_id, metadata)
            return True

        except Exception:
            return False

    async def delete_document(self, doc_id: int) -> bool:
        """
        删除文档

        Args:
            doc_id: 文档ID

        Returns:
            bool: 是否成功删除
        """
        try:
            # FaissVecDB的delete方法需要doc_id(string)
            # 但我们的doc_id是int(内部ID)
            # 需要通过document_storage获取对应的doc_id字符串
            doc_storage = self.faiss_db.document_storage

            # 获取文档信息
            doc = await doc_storage.get_document(doc_id)
            if not doc:
                return False

            # 使用doc_id字符串删除
            str_doc_id = doc["doc_id"]
            await self.faiss_db.delete(str_doc_id)
            return True

        except Exception:
            return False

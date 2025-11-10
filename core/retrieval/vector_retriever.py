"""
向量检索器 - 基于Faiss的向量密集检索
封装AstrBot的FaissVecDB,提供统一的检索接口
"""

from dataclasses import dataclass
from typing import Any

from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

from ..text_processor import TextProcessor


@dataclass
class VectorResult:
    """向量检索结果"""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]


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
        text_processor: TextProcessor | None = None,
        config: dict[str, Any] | None = None,
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
        self, content: str, metadata: dict[str, Any] | None = None
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
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[VectorResult]:
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

    async def update_metadata(self, doc_id: int, metadata: dict[str, Any]) -> bool:
        """
        更新文档元数据（通过重新获取并更新）

        Args:
            doc_id: 文档ID (整数 id)
            metadata: 新的元数据字典

        Returns:
            bool: 是否成功更新
        """
        import json

        from astrbot.api import logger

        try:
            doc_storage = self.faiss_db.document_storage

            # 通过 id 获取文档
            docs = await doc_storage.get_documents(
                metadata_filters={}, ids=[doc_id], limit=1
            )

            if not docs or len(docs) == 0:
                logger.warning(f"[元数据更新] 文档不存在 (doc_id={doc_id})")
                return False

            doc = docs[0]
            uuid_doc_id = doc.get("doc_id")

            if not uuid_doc_id:
                logger.error(f"[元数据更新] 文档缺少 UUID (doc_id={doc_id})")
                return False

            # 获取当前元数据并更新
            current_metadata_str = doc.get("metadata", "{}")
            if isinstance(current_metadata_str, str):
                try:
                    current_metadata = json.loads(current_metadata_str)
                except (json.JSONDecodeError, TypeError):
                    current_metadata = {}
            else:
                current_metadata = current_metadata_str or {}

            # 合并新元数据
            current_metadata.update(metadata)

            # 使用 SQLAlchemy 方式更新（直接操作数据库）
            async with doc_storage.get_session() as session, session.begin():
                from sqlalchemy import text

                await session.execute(
                    text("UPDATE documents SET metadata = :metadata WHERE id = :id"),
                    {"metadata": json.dumps(current_metadata), "id": doc_id},
                )

            logger.debug(f"[元数据更新] 成功 (doc_id={doc_id})")
            return True

        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[元数据更新] 失败 (doc_id={doc_id}): {e}", exc_info=True)
            return False

    async def delete_document(self, doc_id: int) -> bool:
        """
        删除文档（修复版：正确使用 FaissVecDB.delete API）

        Args:
            doc_id: 文档ID (documents表中的整数id)

        Returns:
            bool: 是否成功删除
        """
        from astrbot.api import logger

        try:
            doc_storage = self.faiss_db.document_storage

            # 1. 通过整数 id 获取文档（包含 UUID doc_id）
            docs = await doc_storage.get_documents(
                metadata_filters={}, ids=[doc_id], limit=1
            )

            if not docs or len(docs) == 0:
                logger.warning(f"[向量删除] 文档不存在 (doc_id={doc_id})")
                return False

            doc = docs[0]
            uuid_doc_id = doc.get("doc_id")

            if not uuid_doc_id:
                logger.error(f"[向量删除] 文档缺少 UUID (doc_id={doc_id})")
                return False

            # 2. 使用 UUID 调用 FaissVecDB.delete()
            # 这会同时删除 document_storage 和 embedding_storage
            await self.faiss_db.delete(uuid_doc_id)

            logger.debug(
                f"[向量删除] 成功删除 (doc_id={doc_id}, uuid={uuid_doc_id})"
            )
            return True

        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[向量删除] 失败 (doc_id={doc_id}): {e}", exc_info=True)
            return False

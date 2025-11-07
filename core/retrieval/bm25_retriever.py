# -*- coding: utf-8 -*-
"""
BM25检索器 - 基于SQLite FTS5的稀疏检索
实现简洁的BM25检索功能,用于MemoryEngine的混合检索
"""

import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import aiosqlite

from ..text_processor import TextProcessor


@dataclass
class BM25Result:
    """BM25检索结果"""

    doc_id: int
    score: float
    content: str
    metadata: Dict[str, Any]


class BM25Retriever:
    """
    BM25稀疏检索器

    使用SQLite FTS5实现BM25算法的全文检索。
    主要特性:
    1. 使用TextProcessor进行中文分词和停用词过滤
    2. 支持通过metadata过滤session_id和persona_id
    3. BM25分数自动归一化到[0,1]区间
    """

    def __init__(
        self,
        db_path: str,
        text_processor: TextProcessor,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化BM25检索器

        Args:
            db_path: SQLite数据库路径
            text_processor: 文本处理器实例
            config: 配置字典(可选)
        """
        self.db_path = db_path
        self.text_processor = text_processor
        self.config = config or {}
        self.fts_table = "memories_fts"
        self.doc_table = "documents"

    async def initialize(self):
        """
        初始化FTS5索引

        创建memories_fts虚拟表用于全文检索。
        使用unicode61分词器处理已预处理的文本。
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 创建FTS5虚拟表
            await db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table}
                USING fts5(
                    content,
                    doc_id UNINDEXED,
                    tokenize='unicode61'
                )
            """)
            await db.commit()

    async def add_document(
        self, doc_id: int, content: str, metadata: Optional[Dict[str, Any]] = None
    ):
        """
        添加文档到BM25索引

        Args:
            doc_id: 文档ID
            content: 文档内容(原始文本)
            metadata: 文档元数据(可选,用于过滤但不索引)
        """
        # 使用TextProcessor预处理文本
        tokens = self.text_processor.tokenize(content, remove_stopwords=True)
        processed_content = " ".join(tokens)

        async with aiosqlite.connect(self.db_path) as db:
            # 插入到FTS表
            await db.execute(
                f"INSERT INTO {self.fts_table}(doc_id, content) VALUES (?, ?)",
                (doc_id, processed_content),
            )
            await db.commit()

    async def search(
        self,
        query: str,
        limit: int = 50,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[BM25Result]:
        """
        执行BM25搜索

        Args:
            query: 查询字符串
            limit: 返回结果数量
            session_id: 会话ID过滤(可选)
            persona_id: 人格ID过滤(可选)

        Returns:
            BM25Result列表,按归一化分数降序排列
        """
        if not query or not query.strip():
            return []

        # 预处理查询
        tokens = self.text_processor.tokenize(query, remove_stopwords=True)
        if not tokens:
            return []

        # 构建FTS5查询: 使用OR连接多个token,提高召回率
        # 转义特殊字符
        escaped_tokens = []
        for token in tokens:
            # 转义FTS5特殊字符
            escaped = token.replace('"', '""')
            escaped_tokens.append(f'"{escaped}"')

        # 使用OR连接所有token
        fts_query = " OR ".join(escaped_tokens)

        async with aiosqlite.connect(self.db_path) as db:
            # 执行FTS5 BM25搜索
            # 注意: bm25()返回负数,越大(越接近0)越相关,所以用DESC排序
            cursor = await db.execute(
                f"""
                SELECT doc_id, bm25({self.fts_table}) as score
                FROM {self.fts_table}
                WHERE {self.fts_table} MATCH ?
                ORDER BY score DESC
                LIMIT ?
            """,
                (fts_query, limit * 2),
            )  # 多取一些以备过滤后不足

            fts_results = await cursor.fetchall()

            if not fts_results:
                return []

            # 获取文档详情
            doc_ids = [row[0] for row in fts_results]
            placeholders = ",".join("?" * len(doc_ids))

            cursor = await db.execute(
                f"""
                SELECT id, text, metadata
                FROM {self.doc_table}
                WHERE id IN ({placeholders})
            """,
                doc_ids,
            )

            docs = {}
            async for row in cursor:
                doc_id, text, metadata_json = row
                metadata = json.loads(metadata_json) if metadata_json else {}
                docs[doc_id] = {"text": text, "metadata": metadata}

            # 构建结果列表并应用过滤
            results = []
            for doc_id, bm25_score in fts_results:
                if doc_id not in docs:
                    continue

                doc = docs[doc_id]
                metadata = doc["metadata"]

                # 应用过滤器 - session_id和persona_id已经被_extract_session_uuid处理，直接比较
                if session_id is not None:
                    stored_session_id = metadata.get("session_id")
                    if stored_session_id != session_id:
                        continue
                if persona_id is not None:
                    stored_persona_id = metadata.get("persona_id")
                    if stored_persona_id != persona_id:
                        continue

                results.append(
                    BM25Result(
                        doc_id=doc_id,
                        score=bm25_score,
                        content=doc["text"],
                        metadata=metadata,
                    )
                )

                # 达到limit后停止
                if len(results) >= limit:
                    break

            # 归一化分数到[0, 1]
            if results:
                # FTS5的BM25分数是负数,越大(越接近0)越相关
                scores = [r.score for r in results]
                max_score = max(scores)
                min_score = min(scores)
                score_range = max_score - min_score if max_score != min_score else 1.0

                for result in results:
                    # 归一化: (score - min) / range
                    result.score = (result.score - min_score) / score_range

            return results

    async def delete_document(self, doc_id: int) -> bool:
        """
        从BM25索引删除文档

        Args:
            doc_id: 文档ID

        Returns:
            bool: 是否成功删除
        """
        from astrbot.api import logger

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    f"DELETE FROM {self.fts_table} WHERE doc_id = ?", (doc_id,)
                )
                await db.commit()
                return True

        except Exception as e:
            logger.error(f"BM25删除失败 (doc_id={doc_id}): {e}")
            return False

    async def update_document(
        self, doc_id: int, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        更新BM25索引中的文档（重新索引）
        
        Args:
            doc_id: 文档ID
            content: 新内容
            metadata: 新元数据（当前仅用于日志）
        
        Returns:
            bool: 是否成功更新
        """
        from astrbot.api import logger
        
        try:
            # 重新处理内容
            tokens = self.text_processor.tokenize(content, remove_stopwords=True)
            processed_content = " ".join(tokens)
            
            async with aiosqlite.connect(self.db_path) as db:
                # 先删除旧索引
                await db.execute(
                    f"DELETE FROM {self.fts_table} WHERE doc_id = ?", (doc_id,)
                )
                
                # 插入新索引
                await db.execute(
                    f"INSERT INTO {self.fts_table}(doc_id, content) VALUES (?, ?)",
                    (doc_id, processed_content),
                )
                
                await db.commit()
                logger.debug(f"[BM25] 成功更新文档索引 doc_id={doc_id}")
                return True
                
        except Exception as e:
            logger.error(f"[BM25] 更新文档失败 (doc_id={doc_id}): {e}")
            return False

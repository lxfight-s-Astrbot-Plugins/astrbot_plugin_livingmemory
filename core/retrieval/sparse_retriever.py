# -*- coding: utf-8 -*-
"""
稀疏检索器 - 基于 SQLite FTS5 和 BM25 的全文检索
"""

import json
import sqlite3
import math
import re
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass
import asyncio
import aiosqlite

from astrbot.api import logger
from ..utils.stopwords_manager import StopwordsManager

try:
    import jieba

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    logger.warning("jieba not available, Chinese tokenization disabled")


@dataclass
class SparseResult:
    """稀疏检索结果"""

    doc_id: int
    score: float
    content: str
    metadata: Dict[str, Any]


class FTSManager:
    """FTS5 索引管理器"""

    def __init__(
        self, db_path: str, stopwords_manager: Optional[StopwordsManager] = None
    ):
        self.db_path = db_path
        self.fts_table_name = "documents_fts"
        self.stopwords_manager = stopwords_manager
        self.use_stopwords = stopwords_manager is not None

    async def initialize(self):
        """初始化 FTS5 索引"""
        async with aiosqlite.connect(self.db_path) as db:
            # 启用 FTS5 扩展
            await db.execute("PRAGMA foreign_keys = ON")

            # 创建 FTS5 虚拟表
            await db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name} 
                USING fts5(content, doc_id, tokenize='unicode61')
            """)

            # 创建触发器，保持同步
            await self._create_triggers(db)

            await db.commit()
            logger.info(f"FTS5 index initialized: {self.fts_table_name}")

    async def _create_triggers(self, db: aiosqlite.Connection):
        """创建数据同步触发器（已移除 - 改为手动插入以支持预处理）"""
        # 注意：触发器已移除，改为在 SparseRetriever.add_document() 中手动同步
        # 这样可以在插入前进行分词和停用词过滤
        pass

    async def rebuild_index(self):
        """重建索引"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"DELETE FROM {self.fts_table_name}")
            # 注意：需要手动同步，因为触发器已移除
            # 这个方法应该由 SparseRetriever 调用
            await db.commit()
            logger.info("FTS index cleared (需要手动重新索引所有文档)")

    def preprocess_text(self, text: str) -> str:
        """
        预处理文本：分词 + 停用词过滤

        Args:
            text: 原始文本

        Returns:
            str: 预处理后的文本（空格分隔的tokens）
        """
        if not text or not text.strip():
            return ""

        # 1. 移除多余空白
        text = " ".join(text.split())

        # 2. 中文分词
        if JIEBA_AVAILABLE:
            # 检查是否包含中文
            if any("\u4e00" <= char <= "\u9fff" for char in text):
                tokens = list(jieba.cut_for_search(text))
            else:
                # 非中文，按空格分词
                tokens = text.split()
        else:
            tokens = text.split()

        # 3. 去除停用词和标点
        if self.use_stopwords and self.stopwords_manager:
            filtered_tokens = []
            for token in tokens:
                # 跳过空token
                if not token or token.isspace():
                    continue
                # 跳过纯标点
                if all(not c.isalnum() for c in token):
                    continue
                # 跳过停用词
                if not self.stopwords_manager.is_stopword(token):
                    filtered_tokens.append(token)
            tokens = filtered_tokens
        else:
            # 即使不用停用词，也要过滤空白和纯标点
            tokens = [
                t
                for t in tokens
                if t and not t.isspace() and any(c.isalnum() for c in t)
            ]

        # 4. 返回空格分隔的tokens
        return " ".join(tokens)

    async def add_document(self, doc_id: int, content: str):
        """
        添加单个文档到 FTS 索引

        Args:
            doc_id: 文档 ID
            content: 文档内容
        """
        processed_content = self.preprocess_text(content)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"INSERT INTO {self.fts_table_name}(doc_id, content) VALUES (?, ?)",
                (doc_id, processed_content),
            )
            await db.commit()
            logger.debug(
                f"FTS文档已添加: ID={doc_id}, 原始长度={len(content)}, 处理后={len(processed_content)}"
            )

    async def update_document(self, doc_id: int, content: str):
        """
        更新文档内容

        Args:
            doc_id: 文档 ID
            content: 新内容
        """
        processed_content = self.preprocess_text(content)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"DELETE FROM {self.fts_table_name} WHERE doc_id = ?", (doc_id,)
            )
            await db.execute(
                f"INSERT INTO {self.fts_table_name}(doc_id, content) VALUES (?, ?)",
                (doc_id, processed_content),
            )
            await db.commit()
            logger.debug(f"FTS文档已更新: ID={doc_id}")

    async def delete_document(self, doc_id: int):
        """
        删除文档

        Args:
            doc_id: 文档 ID
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"DELETE FROM {self.fts_table_name} WHERE doc_id = ?", (doc_id,)
            )
            await db.commit()
            logger.debug(f"FTS文档已删除: ID={doc_id}")

    async def search(self, query: str, limit: int = 50) -> List[Tuple[int, float]]:
        """执行 BM25 搜索"""
        async with aiosqlite.connect(self.db_path) as db:
            # 将整个查询用双引号包裹，以处理特殊字符并将其作为短语搜索
            # 这是为了防止 FTS5 语法错误，例如 'syntax error near "."'
            safe_query = f'"{query}"'

            # 使用 BM25 算法搜索
            cursor = await db.execute(
                f"""
                SELECT doc_id, bm25({self.fts_table_name}) as score 
                FROM {self.fts_table_name} 
                WHERE {self.fts_table_name} MATCH ?
                ORDER BY score
                LIMIT ?
            """,
                (safe_query, limit),
            )

            results = await cursor.fetchall()
            return [(row[0], row[1]) for row in results]


class SparseRetriever:
    """稀疏检索器"""

    def __init__(self, db_path: str, config: Dict[str, Any] = None):
        self.db_path = db_path
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.use_chinese_tokenizer = self.config.get(
            "use_chinese_tokenizer", JIEBA_AVAILABLE
        )

        # 停用词配置
        self.enable_stopwords = self.config.get("enable_stopwords_filtering", True)
        self.stopwords_source = self.config.get("stopwords_source", "hit")
        self.custom_stopwords = self.config.get("custom_stopwords", [])
        self.stopwords_manager: Optional[StopwordsManager] = None

        # 初始化 FTS 管理器（稍后会设置 stopwords_manager）
        self.fts_manager: Optional[FTSManager] = None

        logger.info("SparseRetriever 初始化")
        logger.info(f"  启用状态: {'是' if self.enabled else '否'}")
        logger.info(
            f"  中文分词: {'是' if self.use_chinese_tokenizer else '否'} (jieba {'可用' if JIEBA_AVAILABLE else '不可用'})"
        )
        logger.info(f"  停用词过滤: {'是' if self.enable_stopwords else '否'}")
        logger.info(f"  停用词来源: {self.stopwords_source}")
        logger.info(f"  自定义停用词: {len(self.custom_stopwords)} 个")
        logger.info(f"  数据库路径: {db_path}")

    async def initialize(self):
        """初始化稀疏检索器"""
        if not self.enabled:
            logger.info("稀疏检索器已禁用，跳过初始化")
            return

        logger.info("开始初始化稀疏检索器...")

        try:
            # 1. 初始化停用词管理器
            if self.enable_stopwords:
                logger.info("初始化停用词管理器...")
                self.stopwords_manager = StopwordsManager()
                await self.stopwords_manager.load_stopwords(
                    source=self.stopwords_source,
                    custom_words=self.custom_stopwords,
                    auto_download=True,
                )
                logger.info(
                    f"✅ 停用词管理器初始化成功，共 {len(self.stopwords_manager.stopwords)} 个停用词"
                )
            else:
                logger.info("停用词过滤已禁用")
                self.stopwords_manager = None

            # 2. 初始化 FTS 管理器
            self.fts_manager = FTSManager(self.db_path, self.stopwords_manager)
            await self.fts_manager.initialize()
            logger.info("✅ FTS5 索引初始化成功")

        except Exception as e:
            logger.error(
                f"❌ 稀疏检索器初始化失败: {type(e).__name__}: {e}", exc_info=True
            )
            raise

        # 如果启用中文分词，初始化 jieba
        if self.use_chinese_tokenizer and JIEBA_AVAILABLE:
            logger.debug("jieba 中文分词已启用")
            # 可以添加自定义词典
            pass

        logger.info("✅ 稀疏检索器初始化完成")

    def _preprocess_query(self, query: str) -> str:
        """
        预处理查询，包括分词和安全转义。

        Args:
            query: 原始查询字符串

        Returns:
            str: 处理后的安全查询字符串
        """
        if not query or not query.strip():
            return ""

        # 使用 FTSManager 的预处理方法
        if self.fts_manager:
            processed = self.fts_manager.preprocess_text(query)
        else:
            processed = query.strip()

        # FTS5 安全转义: 双引号需要转义为两个双引号
        processed = processed.replace('"', '""')

        return processed

    async def search(
        self,
        query: str,
        limit: int = 50,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
        metadata_filters: Optional[Dict[str, Any]] = None,
    ) -> List[SparseResult]:
        """执行稀疏检索"""
        if not self.enabled:
            logger.debug("稀疏检索器未启用，返回空结果")
            return []

        logger.debug(f"稀疏检索: query='{query[:50]}...', limit={limit}")

        try:
            # 预处理查询
            processed_query = self._preprocess_query(query)
            logger.debug(f"  原始查询: '{query[:50]}...'")
            logger.debug(f"  处理后查询: '{processed_query[:50]}...'")

            # 执行 FTS 搜索
            fts_results = await self.fts_manager.search(processed_query, limit)

            if not fts_results:
                logger.debug("  FTS 搜索无结果")
                return []

            logger.debug(f"  FTS 返回 {len(fts_results)} 条原始结果")

            # 获取完整的文档信息
            doc_ids = [doc_id for doc_id, _ in fts_results]
            logger.debug(f"  获取文档详情: {len(doc_ids)} 个 ID")
            documents = await self._get_documents(doc_ids)
            logger.debug(f"  成功获取 {len(documents)} 个文档")

            # 应用过滤器
            filtered_results = []
            for doc_id, bm25_score in fts_results:
                if doc_id in documents:
                    doc = documents[doc_id]

                    # 检查元数据过滤器
                    if self._apply_filters(
                        doc.get("metadata", {}),
                        session_id,
                        persona_id,
                        metadata_filters,
                    ):
                        result = SparseResult(
                            doc_id=doc_id,
                            score=bm25_score,
                            content=doc["text"],
                            metadata=doc["metadata"],
                        )
                        filtered_results.append(result)

            logger.debug(f"  过滤后剩余 {len(filtered_results)} 条结果")

            # 归一化 BM25 分数（转换为 0-1）
            if filtered_results:
                max_score = max(r.score for r in filtered_results)
                min_score = min(r.score for r in filtered_results)
                score_range = max_score - min_score if max_score != min_score else 1

                logger.debug(
                    f"  归一化分数: min={min_score:.3f}, max={max_score:.3f}, range={score_range:.3f}"
                )

                for result in filtered_results:
                    original_score = result.score
                    result.score = (result.score - min_score) / score_range
                    logger.debug(
                        f"    ID={result.doc_id}: {original_score:.3f} -> {result.score:.3f}"
                    )

            logger.info(f"✅ 稀疏检索完成，返回 {len(filtered_results)} 条结果")
            return filtered_results

        except Exception as e:
            logger.error(f"❌ 稀疏检索失败: {type(e).__name__}: {e}", exc_info=True)
            logger.error(f"  失败上下文: query='{query[:50]}...', limit={limit}")
            return []

    async def _get_documents(self, doc_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """批量获取文档"""
        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ",".join("?" for _ in doc_ids)
            cursor = await db.execute(
                f"""
                SELECT id, text, metadata FROM documents WHERE id IN ({placeholders})
            """,
                doc_ids,
            )

            documents = {}
            async for row in cursor:
                metadata = json.loads(row[2]) if isinstance(row[2], str) else row[2]
                documents[row[0]] = {"text": row[1], "metadata": metadata or {}}

            return documents

    def _apply_filters(
        self,
        metadata: Dict[str, Any],
        session_id: Optional[str],
        persona_id: Optional[str],
        metadata_filters: Optional[Dict[str, Any]],
    ) -> bool:
        """应用过滤器"""
        # 会话过滤
        if session_id and metadata.get("session_id") != session_id:
            return False

        # 人格过滤
        if persona_id and metadata.get("persona_id") != persona_id:
            return False

        # 自定义元数据过滤
        if metadata_filters:
            for key, value in metadata_filters.items():
                if metadata.get(key) != value:
                    return False

        return True

    async def add_document(self, doc_id: int, content: str):
        """
        添加文档到 FTS 索引

        Args:
            doc_id: 文档 ID
            content: 文档内容
        """
        if not self.enabled or not self.fts_manager:
            return

        await self.fts_manager.add_document(doc_id, content)

    async def update_document(self, doc_id: int, content: str):
        """
        更新 FTS 索引中的文档

        Args:
            doc_id: 文档 ID
            content: 新内容
        """
        if not self.enabled or not self.fts_manager:
            return

        await self.fts_manager.update_document(doc_id, content)

    async def delete_document(self, doc_id: int):
        """
        从 FTS 索引删除文档

        Args:
            doc_id: 文档 ID
        """
        if not self.enabled or not self.fts_manager:
            return

        await self.fts_manager.delete_document(doc_id)

    async def rebuild_index(self):
        """重建索引（从 documents 表同步所有数据）"""
        if not self.enabled:
            logger.warning("稀疏检索器未启用，无法重建索引")
            return

        logger.info("🔄 开始重建 FTS5 索引...")

        try:
            # 1. 清空现有索引
            await self.fts_manager.rebuild_index()

            # 2. 从 documents 表重新加载所有数据
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT id, text FROM documents")
                rows = await cursor.fetchall()

                logger.info(f"找到 {len(rows)} 个文档需要索引")

                # 3. 批量添加文档
                for row in rows:
                    doc_id, content = row
                    await self.fts_manager.add_document(doc_id, content)

            logger.info(f"✅ FTS5 索引重建成功，已索引 {len(rows)} 个文档")

        except Exception as e:
            logger.error(
                f"❌ 重建 FTS5 索引失败: {type(e).__name__}: {e}", exc_info=True
            )
            raise

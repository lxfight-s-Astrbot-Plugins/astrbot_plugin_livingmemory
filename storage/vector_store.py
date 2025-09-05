# -*- coding: utf-8 -*-
import os
import asyncio
import faiss
import numpy as np
from typing import List, Tuple, Optional

from astrbot.api import logger


class VectorStore:
    """
    专门管理 Faiss 索引，处理向量的增、删、查。
    """

    def __init__(self, index_path: str, dimension: int):
        self.index_path = index_path
        self.dimension = dimension
        self.index: Optional[faiss.Index] = None
        # 注意：_load_index 现在是异步的，需要在初始化后手动调用
        # 或者创建一个异步的初始化方法

    async def _load_index(self):
        """
        从文件加载 Faiss 索引，如果不存在则创建一个新的。
        """
        if os.path.exists(self.index_path):
            logger.info(f"Loading Faiss index from {self.index_path}")
            self.index = await asyncio.to_thread(faiss.read_index, self.index_path)
        else:
            logger.info(f"Creating new Faiss index. Dimension: {self.dimension}")
            # 使用 IndexFlatL2 作为基础索引，这是常用的欧氏距离索引
            base_index = faiss.IndexFlatL2(self.dimension)
            # 使用 IndexIDMap2 将我们的自定义整数 ID 映射到向量
            self.index = faiss.IndexIDMap2(base_index)

    async def save_index(self):
        """
        将当前索引状态保存到文件。
        """
        logger.info(f"Saving Faiss index to {self.index_path}")
        await asyncio.to_thread(faiss.write_index, self.index, self.index_path)

    async def add(self, ids: List[int], embeddings: List[List[float]]):
        """
        将带有自定义 ID 的向量添加到索引中。
        """
        if not ids:
            return
        # Faiss 需要 int64 类型的 ID 和 float32 类型的向量
        ids_np = np.array(ids, dtype=np.int64)
        embeddings_np = np.array(embeddings, dtype=np.float32)
        await asyncio.to_thread(self.index.add_with_ids, embeddings_np, ids_np)

    async def search(
        self, query_embedding: List[float], k: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        在索引中搜索最相似的 k 个向量。
        返回 (距离数组, ID数组)。
        """
        query_np = np.array([query_embedding], dtype=np.float32)
        # self.index.ntotal 是索引中的向量总数
        k = min(k, self.index.ntotal)
        if k == 0:
            return np.array([]), np.array([])
        distances, ids = await asyncio.to_thread(self.index.search, query_np, k)
        return distances[0], ids[0]

    async def remove(self, ids_to_remove: List[int]):
        """
        从索引中移除指定 ID 的向量。
        """
        if not ids_to_remove:
            return
        ids_np = np.array(ids_to_remove, dtype=np.int64)
        await asyncio.to_thread(self.index.remove_ids, ids_np)

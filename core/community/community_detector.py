# -*- coding: utf-8 -*-
import asyncio
import aiosqlite
import networkx as nx
from typing import List, Tuple

from astrbot.api import logger


class CommunityDetector:
    """
    一个后台服务，负责从 SQLite 加载图数据，
    使用 NetworkX 进行社区发现，并将结果写回数据库。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.connection = None

    async def initialize(self):
        """初始化数据库连接。"""
        self.connection = await aiosqlite.connect(self.db_path)

    async def close(self):
        """关闭数据库连接。"""
        if self.connection:
            await self.connection.close()
            self.connection = None

    async def _load_graph_from_db(self) -> nx.Graph:
        """从 SQLite 中加载边，构建一个 NetworkX 图对象。"""
        G = nx.Graph()
        if not self.connection:
            await self.initialize()

        try:
            # 我们只需要边的信息来构建图的结构
            cursor = await self.connection.execute(
                "SELECT source_id, target_id FROM graph_edges"
            )
            edges = await cursor.fetchall()
            G.add_edges_from(edges)
        except Exception as e:
            logger.error(f"从数据库加载图数据失败: {e}")
            raise

        return G

    async def _save_results_to_db(self, communities: List[Tuple[str]]):
        """将计算出的社区结果批量更新回 memories 表。"""
        if not self.connection:
            logger.error("数据库连接未初始化")
            return

        # 注意：这里的逻辑需要一个从“图节点ID”到“记忆internal_id”的映射
        # 我们简化一下，假设 Event 节点的 ID 就是 memory_id
        updates = []
        for i, community_nodes in enumerate(communities):
            community_id = f"community_{i}"
            for node_id in community_nodes:
                # 假设事件节点的 entity_id 格式为 'evt_mem_xxx'
                if node_id.startswith("evt_mem_"):
                    memory_id = node_id.split("evt_mem_")[1]
                    updates.append((community_id, memory_id))

        if updates:
            try:
                await self.connection.executemany(
                    "UPDATE memories SET community_id = ? WHERE memory_id = ?", updates
                )
                await self.connection.commit()
                logger.info(f"成功更新 {len(updates)} 条记忆的社区信息")
            except Exception as e:
                logger.error(f"更新社区信息失败: {e}")
                raise

    async def run_detection_and_update(self):
        """
        执行社区发现的完整流程。使用进程池进行计算密集型任务。
        """
        try:
            logger.info("开始从数据库加载图...")
            graph = await self._load_graph_from_db()

            if graph.number_of_nodes() == 0:
                logger.info("图中没有节点，跳过社区发现。")
                return

            logger.info(
                f"图加载完成（{graph.number_of_nodes()}个节点，{graph.number_of_edges()}条边），开始运行 Louvain 社区发现算法..."
            )

            # 使用进程池执行计算密集型的社区发现算法
            import concurrent.futures

            with concurrent.futures.ProcessPoolExecutor() as executor:
                # resolution 参数可以调整社区的大小，值越小社区越多越小
                communities = await asyncio.get_event_loop().run_in_executor(
                    executor,
                    nx.community.louvain_communities,
                    graph,
                    1.0,  # resolution
                )

            logger.info(f"发现 {len(communities)} 个社区，开始将结果写回数据库...")
            await self._save_results_to_db(communities)
            logger.info("社区信息更新完成。")

        except Exception as e:
            logger.error(f"社区发现过程中发生错误: {e}", exc_info=True)
            raise

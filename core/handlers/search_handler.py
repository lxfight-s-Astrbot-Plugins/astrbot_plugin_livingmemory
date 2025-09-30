# -*- coding: utf-8 -*-
"""
search_handler.py - 搜索管理业务逻辑
处理记忆搜索、稀疏检索测试等业务逻辑
"""

import json
from typing import Optional, Dict, Any, List

from astrbot.api import logger
from astrbot.api.star import Context

from .base_handler import BaseHandler


class SearchHandler(BaseHandler):
    """搜索管理业务逻辑处理器"""
    
    def __init__(self, context: Context, config: Dict[str, Any], recall_engine=None, sparse_retriever=None):
        super().__init__(context, config)
        self.recall_engine = recall_engine
        self.sparse_retriever = sparse_retriever
    
    async def process(self, *args, **kwargs) -> Dict[str, Any]:
        """处理请求的抽象方法实现"""
        return self.create_response(True, "SearchHandler process method")
    
    async def search_memories(self, query: str, k: int = 3) -> Dict[str, Any]:
        """搜索记忆"""
        if not self.recall_engine:
            return self.create_response(False, "回忆引擎尚未初始化")

        # 输入验证
        if not query or not query.strip():
            return self.create_response(False, "查询内容不能为空")

        if k <= 0:
            return self.create_response(False, "返回数量必须大于0")

        try:
            # 获取session_id和persona_id
            try:
                session_id = await self.context.conversation_manager.get_curr_conversation_id(None)
            except Exception as e:
                logger.warning(f"获取会话ID失败: {e}")
                session_id = None

            try:
                from ..utils import get_persona_id
                persona_id = await get_persona_id(self.context, None)
            except Exception as e:
                logger.warning(f"获取人格ID失败: {e}")
                persona_id = None

            results = await self.recall_engine.recall(
                self.context, query, session_id, persona_id, k
            )

            if not results:
                return self.create_response(True, f"未能找到与 '{query}' 相关的记忆", [])

            # 格式化搜索结果
            formatted_results = []
            for res in results:
                formatted_results.append({
                    "id": res.data.get('id', 'unknown'),
                    "similarity": res.similarity,
                    "text": res.data.get('text', ''),
                    "metadata": self.safe_parse_metadata(res.data.get("metadata", {}))
                })

            return self.create_response(True, f"为您找到 {len(results)} 条相关记忆", formatted_results)

        except Exception as e:
            logger.error(f"搜索记忆时发生错误: {e}", exc_info=True)
            return self.create_response(False, f"搜索记忆时发生错误: {e}")

    async def test_sparse_search(self, query: str, k: int = 5) -> Dict[str, Any]:
        """测试稀疏检索功能"""
        if not self.sparse_retriever:
            return self.create_response(False, "稀疏检索器未启用")

        # 输入验证
        if not query or not query.strip():
            return self.create_response(False, "查询内容不能为空")

        if k <= 0:
            return self.create_response(False, "返回数量必须大于0")

        try:
            results = await self.sparse_retriever.search(query=query, limit=k)
            
            if not results:
                return self.create_response(True, f"未找到与 '{query}' 相关的记忆", [])

            # 格式化搜索结果
            formatted_results = []
            for res in results:
                formatted_results.append({
                    "doc_id": res.doc_id,
                    "score": res.score,
                    "content": res.content,
                    "metadata": res.metadata
                })
            
            return self.create_response(True, f"找到 {len(results)} 条稀疏检索结果", formatted_results)

        except Exception as e:
            logger.error(f"稀疏检索测试失败: {e}", exc_info=True)
            return self.create_response(False, f"稀疏检索测试失败: {e}")

    async def rebuild_sparse_index(self) -> Dict[str, Any]:
        """重建稀疏检索索引"""
        if not self.sparse_retriever:
            return self.create_response(False, "稀疏检索器未启用")

        try:
            await self.sparse_retriever.rebuild_index()
            return self.create_response(True, "稀疏检索索引重建完成")
        except Exception as e:
            logger.error(f"重建稀疏索引失败: {e}", exc_info=True)
            return self.create_response(False, f"重建稀疏索引失败: {e}")

    def format_search_results_for_display(self, response: Dict[str, Any]) -> str:
        """格式化搜索结果用于显示"""
        if not response.get("success"):
            return response.get("message", "搜索失败")
        
        data = response.get("data", [])
        message = response.get("message", "")
        
        response_parts = [message]
        
        for res in data:
            metadata = res.get("metadata", {})
            create_time_str = self.format_timestamp(metadata.get("create_time"))
            last_access_time_str = self.format_timestamp(metadata.get("last_access_time"))
            importance_score = metadata.get("importance", 0.0)
            event_type = metadata.get("event_type", "未知")

            card = (
                f"ID: {res['id']}\n"
                f"记 忆 度: {res['similarity']:.2f}\n"
                f"重 要 性: {importance_score:.2f}\n"
                f"记忆类型: {event_type}\n\n"
                f"内容: {res['text']}\n\n"
                f"创建于: {create_time_str}\n"
                f"最后访问: {last_access_time_str}"
            )
            response_parts.append(card)

        return "\n\n".join(response_parts)

    def format_sparse_results_for_display(self, response: Dict[str, Any]) -> str:
        """格式化稀疏检索结果用于显示"""
        if not response.get("success"):
            return response.get("message", "搜索失败")
        
        data = response.get("data", [])
        message = response.get("message", "")
        
        response_parts = [message]
        
        for i, res in enumerate(data, 1):
            response_parts.append(f"\n{i}. [ID: {res['doc_id']}] Score: {res['score']:.3f}")
            response_parts.append(f"   内容: {res['content'][:100]}{'...' if len(res['content']) > 100 else ''}")
            
            # 显示元数据
            metadata = res.get("metadata", {})
            if metadata.get("event_type"):
                response_parts.append(f"   类型: {metadata['event_type']}")
            if metadata.get("importance"):
                response_parts.append(f"   重要性: {metadata['importance']:.2f}")

        return "\n".join(response_parts)
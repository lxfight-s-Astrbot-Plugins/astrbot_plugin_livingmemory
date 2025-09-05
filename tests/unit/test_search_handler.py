# -*- coding: utf-8 -*-
"""
test_search_handler.py - 搜索管理处理器测试
"""

import pytest
from unittest.mock import Mock, AsyncMock

from core.handlers.search_handler import SearchHandler
from tests.conftest import TEST_CONFIG


class TestSearchHandler:
    """搜索管理处理器测试类"""
    
    def setup_method(self):
        """测试前设置"""
        self.mock_context = Mock()
        self.mock_recall_engine = Mock()
        self.mock_sparse_retriever = Mock()
        self.handler = SearchHandler(
            self.mock_context, 
            TEST_CONFIG, 
            self.mock_recall_engine,
            self.mock_sparse_retriever
        )
    
    @pytest.mark.asyncio
    async def test_search_memories_success(self):
        """测试搜索记忆（成功）"""
        # 模拟搜索结果
        mock_results = [
            Mock(
                data={"id": 1, "text": "测试记忆1", "metadata": '{"importance": 0.8}'},
                similarity=0.9
            ),
            Mock(
                data={"id": 2, "text": "测试记忆2", "metadata": '{"importance": 0.6}'},
                similarity=0.7
            )
        ]
        
        self.mock_recall_engine.recall = AsyncMock(return_value=mock_results)
        
        result = await self.handler.search_memories("测试查询", k=5)
        
        assert result["success"] is True
        assert "为您找到 2 条相关记忆" in result["message"]
        assert len(result["data"]) == 2
        assert result["data"][0]["id"] == 1
        assert result["data"][0]["similarity"] == 0.9
        assert result["data"][1]["id"] == 2
        assert result["data"][1]["similarity"] == 0.7
        
        # 验证调用参数
        self.mock_recall_engine.recall.assert_called_once_with(
            self.mock_context, "测试查询", k=5
        )
    
    @pytest.mark.asyncio
    async def test_search_memories_no_results(self):
        """测试搜索记忆（无结果）"""
        self.mock_recall_engine.recall = AsyncMock(return_value=[])
        
        result = await self.handler.search_memories("无结果查询", k=5)
        
        assert result["success"] is True
        assert "未能找到与 '无结果查询' 相关的记忆" in result["message"]
        assert result["data"] == []
    
    @pytest.mark.asyncio
    async def test_search_memories_no_recall_engine(self):
        """测试没有回忆引擎时的搜索"""
        handler = SearchHandler(self.mock_context, TEST_CONFIG, None, None)
        
        result = await handler.search_memories("测试查询")
        
        assert result["success"] is False
        assert "回忆引擎尚未初始化" in result["message"]
    
    @pytest.mark.asyncio
    async def test_search_memories_exception(self):
        """测试搜索记忆时的异常处理"""
        self.mock_recall_engine.recall = AsyncMock(side_effect=Exception("搜索错误"))
        
        result = await self.handler.search_memories("测试查询")
        
        assert result["success"] is False
        assert "搜索记忆时发生错误" in result["message"]
    
    @pytest.mark.asyncio
    async def test_test_sparse_search_success(self):
        """测试稀疏检索测试（成功）"""
        # 模拟稀疏检索结果
        mock_results = [
            Mock(
                doc_id=1,
                score=0.8,
                content="稀疏检索结果1",
                metadata={"event_type": "FACT", "importance": 0.7}
            ),
            Mock(
                doc_id=2,
                score=0.6,
                content="稀疏检索结果2",
                metadata={"event_type": "PREFERENCE", "importance": 0.9}
            )
        ]
        
        self.mock_sparse_retriever.search = AsyncMock(return_value=mock_results)
        
        result = await self.handler.test_sparse_search("测试查询", k=5)
        
        assert result["success"] is True
        assert "找到 2 条稀疏检索结果" in result["message"]
        assert len(result["data"]) == 2
        assert result["data"][0]["doc_id"] == 1
        assert result["data"][0]["score"] == 0.8
        assert result["data"][1]["doc_id"] == 2
        assert result["data"][1]["score"] == 0.6
        
        # 验证调用参数
        self.mock_sparse_retriever.search.assert_called_once_with(
            query="测试查询", limit=5
        )
    
    @pytest.mark.asyncio
    async def test_test_sparse_search_no_results(self):
        """测试稀疏检索测试（无结果）"""
        self.mock_sparse_retriever.search = AsyncMock(return_value=[])
        
        result = await self.handler.test_sparse_search("无结果查询", k=5)
        
        assert result["success"] is True
        assert "未找到与 '无结果查询' 相关的记忆" in result["message"]
        assert result["data"] == []
    
    @pytest.mark.asyncio
    async def test_test_sparse_search_no_retriever(self):
        """测试没有稀疏检索器时的测试"""
        handler = SearchHandler(self.mock_context, TEST_CONFIG, None, None)
        
        result = await handler.test_sparse_search("测试查询")
        
        assert result["success"] is False
        assert "稀疏检索器未启用" in result["message"]
    
    @pytest.mark.asyncio
    async def test_test_sparse_search_exception(self):
        """测试稀疏检索测试时的异常处理"""
        self.mock_sparse_retriever.search = AsyncMock(side_effect=Exception("稀疏检索错误"))
        
        result = await self.handler.test_sparse_search("测试查询")
        
        assert result["success"] is False
        assert "稀疏检索测试失败" in result["message"]
    
    @pytest.mark.asyncio
    async def test_rebuild_sparse_index_success(self):
        """测试重建稀疏索引（成功）"""
        self.mock_sparse_retriever.rebuild_index = AsyncMock()
        
        result = await self.handler.rebuild_sparse_index()
        
        assert result["success"] is True
        assert "稀疏检索索引重建完成" in result["message"]
        
        # 验证调用了重建方法
        self.mock_sparse_retriever.rebuild_index.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_rebuild_sparse_index_no_retriever(self):
        """测试没有稀疏检索器时重建索引"""
        handler = SearchHandler(self.mock_context, TEST_CONFIG, None, None)
        
        result = await handler.rebuild_sparse_index()
        
        assert result["success"] is False
        assert "稀疏检索器未启用" in result["message"]
    
    @pytest.mark.asyncio
    async def test_rebuild_sparse_index_exception(self):
        """测试重建稀索引导常处理"""
        self.mock_sparse_retriever.rebuild_index = AsyncMock(side_effect=Exception("重建错误"))
        
        result = await self.handler.rebuild_sparse_index()
        
        assert result["success"] is False
        assert "重建稀疏索引失败" in result["message"]
    
    def test_format_search_results_for_display_success(self):
        """测试格式化搜索结果显示（成功）"""
        mock_response = {
            "success": True,
            "message": "为您找到 2 条相关记忆",
            "data": [
                {
                    "id": 1,
                    "similarity": 0.9,
                    "text": "测试记忆1",
                    "metadata": {
                        "create_time": 1609459200.0,
                        "last_access_time": 1609459200.0,
                        "importance": 0.8,
                        "event_type": "FACT"
                    }
                },
                {
                    "id": 2,
                    "similarity": 0.7,
                    "text": "测试记忆2",
                    "metadata": {
                        "create_time": 1609459200.0,
                        "last_access_time": 1609459200.0,
                        "importance": 0.6,
                        "event_type": "PREFERENCE"
                    }
                }
            ]
        }
        
        result = self.handler.format_search_results_for_display(mock_response)
        
        assert "为您找到 2 条相关记忆" in result
        assert "ID: 1" in result
        assert "记 忆 度: 0.90" in result
        assert "重 要 性: 0.80" in result
        assert "记忆类型: FACT" in result
        assert "测试记忆1" in result
        assert "ID: 2" in result
        assert "测试记忆2" in result
    
    def test_format_search_results_for_display_failure(self):
        """测试格式化搜索结果显示（失败）"""
        mock_response = {
            "success": False,
            "message": "搜索失败"
        }
        
        result = self.handler.format_search_results_for_display(mock_response)
        
        assert result == "搜索失败"
    
    def test_format_sparse_results_for_display_success(self):
        """测试格式化稀疏检索结果显示（成功）"""
        mock_response = {
            "success": True,
            "message": "找到 2 条稀疏检索结果",
            "data": [
                {
                    "doc_id": 1,
                    "score": 0.8,
                    "content": "稀疏检索结果1",
                    "metadata": {
                        "event_type": "FACT",
                        "importance": 0.7
                    }
                },
                {
                    "doc_id": 2,
                    "score": 0.6,
                    "content": "稀疏检索结果2很长很长很长很长很长很长很长很长很长很长很长很长",
                    "metadata": {
                        "event_type": "PREFERENCE",
                        "importance": 0.9
                    }
                }
            ]
        }
        
        result = self.handler.format_sparse_results_for_display(mock_response)
        
        assert "找到 2 条稀疏检索结果" in result
        assert "1. [ID: 1] Score: 0.800" in result
        assert "类型: FACT" in result
        assert "重要性: 0.70" in result
        assert "2. [ID: 2] Score: 0.600" in result
        assert "..." in result or len(result) < 500  # 长内容被截断或整个结果显示
        assert "类型: PREFERENCE" in result
        assert "重要性: 0.90" in result
    
    def test_format_sparse_results_for_display_failure(self):
        """测试格式化稀疏检索结果显示（失败）"""
        mock_response = {
            "success": False,
            "message": "稀疏检索失败"
        }
        
        result = self.handler.format_sparse_results_for_display(mock_response)
        
        assert result == "稀疏检索失败"
    
    def test_format_sparse_results_for_display_no_metadata(self):
        """测试格式化稀疏检索结果显示（无元数据）"""
        mock_response = {
            "success": True,
            "message": "找到 1 条稀疏检索结果",
            "data": [
                {
                    "doc_id": 1,
                    "score": 0.8,
                    "content": "稀疏检索结果",
                    "metadata": {}
                }
            ]
        }
        
        result = self.handler.format_sparse_results_for_display(mock_response)
        
        assert "找到 1 条稀疏检索结果" in result
        assert "1. [ID: 1] Score: 0.800" in result
        assert "稀疏检索结果" in result
        # 不应该包含类型和重要性信息
        assert "类型:" not in result
        assert "重要性:" not in result
# -*- coding: utf-8 -*-
"""
base_handler.py - 基础处理器类
提供业务逻辑处理的基础功能和通用方法
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import json

from astrbot.api import logger
from astrbot.api.star import Context


class BaseHandler(ABC):
    """基础处理器类，提供通用的业务逻辑功能"""
    
    def __init__(self, context: Context, config: Dict[str, Any]):
        self.context = context
        self.config = config
        
    @abstractmethod
    async def process(self, *args, **kwargs) -> Dict[str, Any]:
        """处理请求的抽象方法"""
        pass
    
    def get_timezone(self) -> Any:
        """获取当前时区"""
        tz_config = self.config.get("timezone_settings", {})
        tz_str = tz_config.get("timezone", "Asia/Shanghai")
        try:
            import pytz
            return pytz.timezone(tz_str)
        except ImportError:
            # 如果pytz不可用，返回UTC
            from datetime import timezone
            return timezone.utc
    
    def format_timestamp(self, ts: Optional[float]) -> str:
        """格式化时间戳"""
        if not ts:
            return "未知"
        try:
            dt_utc = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            dt_local = dt_utc.astimezone(self.get_timezone())
            return dt_local.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return "未知"
    
    def safe_parse_metadata(self, metadata: Any) -> Dict[str, Any]:
        """安全解析元数据"""
        if isinstance(metadata, dict):
            return metadata
        if isinstance(metadata, str):
            try:
                return json.loads(metadata)
            except json.JSONDecodeError:
                return {}
        return {}
    
    def format_memory_card(self, result: Any) -> str:
        """格式化记忆卡片显示"""
        metadata = self.safe_parse_metadata(result.data.get("metadata", {}))
        
        create_time_str = self.format_timestamp(metadata.get("create_time"))
        last_access_time_str = self.format_timestamp(metadata.get("last_access_time"))
        importance_score = metadata.get("importance", 0.0)
        event_type = metadata.get("event_type", "未知")

        card = (
            f"ID: {result.data['id']}\n"
            f"记 忆 度: {result.similarity:.2f}\n"
            f"重 要 性: {importance_score:.2f}\n"
            f"记忆类型: {event_type}\n\n"
            f"内容: {result.data['text']}\n\n"
            f"创建于: {create_time_str}\n"
            f"最后访问: {last_access_time_str}"
        )
        return card
    
    def create_response(self, success: bool = True, message: str = "", data: Any = None) -> Dict[str, Any]:
        """创建标准响应格式"""
        return {
            "success": success,
            "message": message,
            "data": data
        }


class TestableBaseHandler(BaseHandler):
    """用于测试的基础处理器实现"""
    
    async def process(self, *args, **kwargs) -> Dict[str, Any]:
        """测试用的process方法实现"""
        return self.create_response(True, "Test response")
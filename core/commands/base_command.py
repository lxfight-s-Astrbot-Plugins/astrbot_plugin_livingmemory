# -*- coding: utf-8 -*-
"""
base_command.py - 基础命令类
提供命令处理的基础功能和通用方法
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import json

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.star import Context


class BaseCommand(ABC):
    """基础命令类，提供通用的命令处理功能"""
    
    def __init__(self, context: Context, config: Dict[str, Any]):
        self.context = context
        self.config = config
        
    @abstractmethod
    def register_commands(self):
        """注册命令的抽象方法"""
        pass
    
    def get_timezone(self) -> Any:
        """获取当前时区"""
        tz_config = self.config.get("timezone_settings", {})
        tz_str = tz_config.get("timezone", "Asia/Shanghai")
        from ..utils import get_now_datetime
        return get_now_datetime(tz_str).tzinfo
    
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
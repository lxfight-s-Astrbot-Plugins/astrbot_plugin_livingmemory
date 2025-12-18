"""
LivingMemory核心模块
提供统一的记忆管理引擎

目录结构:
- base/: 基础组件（异常、配置、常量）
- models/: 数据模型
- managers/: 管理器（会话管理、记忆引擎）
- processors/: 处理器（记忆处理、文本处理）
- validators/: 验证器（索引验证）
- retrieval/: 检索系统
- utils/: 工具函数
"""

# 基础组件
from .base import (
    ConfigManager,
    ConfigurationError,
    DatabaseError,
    InitializationError,
    LivingMemoryException,
    MemoryProcessingError,
    ProviderNotReadyError,
    RetrievalError,
    ValidationError,
)

# 管理器
from .managers import ConversationManager, MemoryEngine

# 数据模型
from .models import MemoryEvent, Message, Session

# 处理器
from .processors import (
    ChatroomContextParser,
    MemoryProcessor,
    TextProcessor,
    store_round_with_length_check,
)

# 验证器
from .validators import IndexValidator

__all__ = [
    # 基础组件
    "ConfigManager",
    "ConfigurationError",
    "DatabaseError",
    "InitializationError",
    "LivingMemoryException",
    "MemoryProcessingError",
    "ProviderNotReadyError",
    "RetrievalError",
    "ValidationError",
    # 数据模型
    "MemoryEvent",
    "Message",
    "Session",
    # 管理器
    "ConversationManager",
    "MemoryEngine",
    # 处理器
    "ChatroomContextParser",
    "MemoryProcessor",
    "TextProcessor",
    "store_round_with_length_check",
    # 验证器
    "IndexValidator",
]

"""
自定义异常定义
"""


class LivingMemoryException(Exception):
    """LivingMemory 插件基础异常"""

    def __init__(self, message: str, error_code: str | None = None):
        self.message = message
        self.error_code = error_code or "UNKNOWN_ERROR"
        super().__init__(self.message)


class InitializationError(LivingMemoryException):
    """初始化错误"""

    def __init__(self, message: str):
        super().__init__(message, "INIT_ERROR")


class ProviderNotReadyError(LivingMemoryException):
    """Provider未就绪错误"""

    def __init__(self, message: str = "Provider未就绪"):
        super().__init__(message, "PROVIDER_NOT_READY")


class ConfigurationError(LivingMemoryException):
    """配置错误"""

    def __init__(self, message: str):
        super().__init__(message, "CONFIG_ERROR")


class MemoryParseError(LivingMemoryException):
    """LLM 响应无法解析为结构化记忆（可重试，不写入主库）"""

    def __init__(self, message: str):
        super().__init__(message, "MEMORY_PARSE_ERROR")


class LowQualityMemoryError(LivingMemoryException):
    """总结质量不达标，默认不写入主库（可重试）"""

    def __init__(self, message: str):
        super().__init__(message, "LOW_QUALITY_MEMORY")

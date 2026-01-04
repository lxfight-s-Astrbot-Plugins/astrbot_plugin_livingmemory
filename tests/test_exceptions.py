"""
测试自定义异常
"""

from core.exceptions import (
    ConfigurationError,
    DatabaseError,
    InitializationError,
    LivingMemoryException,
    MemoryProcessingError,
    ProviderNotReadyError,
    RetrievalError,
    ValidationError,
)


def test_living_memory_exception():
    """测试基础异常"""
    exc = LivingMemoryException("test message", "TEST_CODE")
    assert str(exc) == "test message"
    assert exc.error_code == "TEST_CODE"
    assert exc.message == "test message"


def test_initialization_error():
    """测试初始化错误"""
    exc = InitializationError("init failed")
    assert str(exc) == "init failed"
    assert exc.error_code == "INIT_ERROR"


def test_provider_not_ready_error():
    """测试Provider未就绪错误"""
    exc = ProviderNotReadyError()
    assert "Provider未就绪" in str(exc)
    assert exc.error_code == "PROVIDER_NOT_READY"


def test_database_error():
    """测试数据库错误"""
    exc = DatabaseError("db error")
    assert str(exc) == "db error"
    assert exc.error_code == "DATABASE_ERROR"


def test_retrieval_error():
    """测试检索错误"""
    exc = RetrievalError("retrieval failed")
    assert str(exc) == "retrieval failed"
    assert exc.error_code == "RETRIEVAL_ERROR"


def test_memory_processing_error():
    """测试记忆处理错误"""
    exc = MemoryProcessingError("processing failed")
    assert str(exc) == "processing failed"
    assert exc.error_code == "MEMORY_PROCESSING_ERROR"


def test_configuration_error():
    """测试配置错误"""
    exc = ConfigurationError("config error")
    assert str(exc) == "config error"
    assert exc.error_code == "CONFIG_ERROR"


def test_validation_error():
    """测试验证错误"""
    exc = ValidationError("validation failed")
    assert str(exc) == "validation failed"
    assert exc.error_code == "VALIDATION_ERROR"


def test_exception_inheritance():
    """测试异常继承关系"""
    assert issubclass(InitializationError, LivingMemoryException)
    assert issubclass(ProviderNotReadyError, LivingMemoryException)
    assert issubclass(DatabaseError, LivingMemoryException)
    assert issubclass(RetrievalError, LivingMemoryException)
    assert issubclass(MemoryProcessingError, LivingMemoryException)
    assert issubclass(ConfigurationError, LivingMemoryException)
    assert issubclass(ValidationError, LivingMemoryException)

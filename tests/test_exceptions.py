"""
Tests for plugin exception hierarchy.
"""

from astrbot_plugin_livingmemory.core.base.exceptions import (
    ConfigurationError,
    DatabaseError,
    InitializationError,
    LivingMemoryException,
    MemoryProcessingError,
    ProviderNotReadyError,
    RetrievalError,
    ValidationError,
)


def test_living_memory_exception_fields() -> None:
    exc = LivingMemoryException("boom", "E_TEST")
    assert str(exc) == "boom"
    assert exc.message == "boom"
    assert exc.error_code == "E_TEST"


def test_specialized_exception_error_codes() -> None:
    assert InitializationError("x").error_code == "INIT_ERROR"
    assert ProviderNotReadyError().error_code == "PROVIDER_NOT_READY"
    assert DatabaseError("x").error_code == "DATABASE_ERROR"
    assert RetrievalError("x").error_code == "RETRIEVAL_ERROR"
    assert MemoryProcessingError("x").error_code == "MEMORY_PROCESSING_ERROR"
    assert ConfigurationError("x").error_code == "CONFIG_ERROR"
    assert ValidationError("x").error_code == "VALIDATION_ERROR"


def test_exception_inheritance() -> None:
    assert issubclass(InitializationError, LivingMemoryException)
    assert issubclass(ProviderNotReadyError, LivingMemoryException)
    assert issubclass(DatabaseError, LivingMemoryException)
    assert issubclass(RetrievalError, LivingMemoryException)
    assert issubclass(MemoryProcessingError, LivingMemoryException)
    assert issubclass(ConfigurationError, LivingMemoryException)
    assert issubclass(ValidationError, LivingMemoryException)

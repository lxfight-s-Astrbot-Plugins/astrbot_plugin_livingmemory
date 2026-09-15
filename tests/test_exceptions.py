"""
Tests for plugin exception hierarchy.
"""

from astrbot_plugin_livingmemory.core.base.exceptions import (
    ConfigurationError,
    InitializationError,
    LivingMemoryException,
    ProviderNotReadyError,
)


def test_living_memory_exception_fields() -> None:
    exc = LivingMemoryException("boom", "E_TEST")
    assert str(exc) == "boom"
    assert exc.message == "boom"
    assert exc.error_code == "E_TEST"


def test_specialized_exception_error_codes() -> None:
    assert InitializationError("x").error_code == "INIT_ERROR"
    assert ProviderNotReadyError().error_code == "PROVIDER_NOT_READY"
    assert ConfigurationError("x").error_code == "CONFIG_ERROR"


def test_exception_inheritance() -> None:
    assert issubclass(InitializationError, LivingMemoryException)
    assert issubclass(ProviderNotReadyError, LivingMemoryException)
    assert issubclass(ConfigurationError, LivingMemoryException)

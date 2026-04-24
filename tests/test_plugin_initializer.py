"""
Tests for PluginInitializer state management and provider resolution.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.plugin_initializer import PluginInitializer


@pytest.fixture
def mock_context():
    context = Mock()
    context.get_provider_by_id = Mock(return_value=None)
    context.get_all_embedding_providers = Mock(return_value=[])
    context.get_using_provider = Mock(return_value=None)
    return context


@pytest.fixture
def initializer(mock_context, tmp_path):
    return PluginInitializer(mock_context, ConfigManager(), str(tmp_path))


def test_initializer_default_state(initializer):
    assert initializer.is_initialized is False
    assert initializer.is_failed is False
    assert initializer.error_message is None


@pytest.mark.asyncio
async def test_ensure_initialized_timeout(initializer):
    ok = await initializer.ensure_initialized(timeout=0.1)
    assert ok is False


def test_initialize_providers_with_fallback(monkeypatch, mock_context, tmp_path):
    class DummyEmbeddingProvider:
        pass

    class DummyProvider:
        pass

    # make isinstance checks pass
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.EmbeddingProvider",
        DummyEmbeddingProvider,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.Provider",
        DummyProvider,
    )

    emb = DummyEmbeddingProvider()
    llm = DummyProvider()
    mock_context.get_provider_by_id.return_value = None
    mock_context.get_all_embedding_providers.return_value = [emb]
    mock_context.get_using_provider.return_value = llm

    init = PluginInitializer(mock_context, ConfigManager(), str(tmp_path))
    init._initialize_providers(silent=True)

    assert init.embedding_provider is emb
    assert init.llm_provider is llm


@pytest.mark.asyncio
async def test_wait_for_providers_non_blocking_success(initializer):
    initializer._initialize_providers = Mock()
    initializer.embedding_provider = object()
    initializer.llm_provider = object()

    ok = await initializer._wait_for_providers_non_blocking(max_wait=0.1)
    assert ok is True


@pytest.mark.asyncio
async def test_retry_task_done_callback_clears_state(initializer):
    task = Mock()
    task.done.return_value = True
    task.cancelled.return_value = False
    task.exception.return_value = None
    initializer._retry_task = task

    initializer._on_retry_task_done(task)
    assert initializer._retry_task is None


@pytest.mark.asyncio
async def test_retry_initialization_timeout_sets_actionable_error(initializer):
    initializer._max_provider_attempts = 0
    initializer._provider_check_attempts = 0

    await initializer._retry_initialization()

    assert initializer.is_failed is True
    assert initializer.error_message is not None
    assert "Provider 初始化超时" in initializer.error_message
    assert "请检查 provider_settings 配置" in initializer.error_message


@pytest.mark.asyncio
async def test_complete_initialization_wires_graph_db_and_engine_config(
    monkeypatch, mock_context, tmp_path
):
    created_vec_dbs = []

    class DummyEmbeddingProvider:
        pass

    class DummyProvider:
        pass

    class FakeFaissVecDB:
        def __init__(self, db_path, index_path, embedding_provider):
            self.db_path = db_path
            self.index_path = index_path
            self.embedding_provider = embedding_provider
            created_vec_dbs.append(self)

        async def initialize(self):
            return None

    class FakeDBMigration:
        def __init__(self, db_path):
            self.db_path = db_path

    class FakeMemoryEngine:
        def __init__(
            self, db_path, faiss_db, graph_vector_db, llm_provider=None, config=None
        ):
            self.db_path = db_path
            self.faiss_db = faiss_db
            self.graph_vector_db = graph_vector_db
            self.llm_provider = llm_provider
            self.config = config or {}
            self.text_processor = Mock(async_init=AsyncMock())

        async def initialize(self):
            return None

    class FakeConversationStore:
        def __init__(self, db_path):
            self.db_path = db_path

        async def initialize(self):
            return None

        async def sync_message_counts(self):
            return []

    class FakeConversationManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeMemoryProcessor:
        def __init__(self, provider, context):
            self.provider = provider
            self.context = context

    class FakeIndexValidator:
        def __init__(self, db_path, db):
            self.db_path = db_path
            self.db = db

    class FakeDecayScheduler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def start(self):
            return None

    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.EmbeddingProvider",
        DummyEmbeddingProvider,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.Provider",
        DummyProvider,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.FaissVecDB",
        FakeFaissVecDB,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.DBMigration",
        FakeDBMigration,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.MemoryEngine",
        FakeMemoryEngine,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.ConversationStore",
        FakeConversationStore,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.ConversationManager",
        FakeConversationManager,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.MemoryProcessor",
        FakeMemoryProcessor,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.IndexValidator",
        FakeIndexValidator,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.DecayScheduler",
        FakeDecayScheduler,
    )

    init = PluginInitializer(
        mock_context,
        ConfigManager(
            {
                "migration_settings": {"auto_migrate": False},
                "importance_decay": {"decay_rate": 0},
                "forgetting_agent": {"auto_cleanup_enabled": False},
                "graph_memory": {
                    "enabled": True,
                    "document_route_weight": 0.7,
                    "graph_route_weight": 0.3,
                    "cross_route_bonus": 0.12,
                    "expansion_limit": 12,
                    "max_topics_per_memory": 4,
                    "max_participants_per_memory": 5,
                    "max_facts_per_memory": 6,
                },
            }
        ),
        str(tmp_path),
    )
    init.embedding_provider = DummyEmbeddingProvider()
    init.llm_provider = DummyProvider()
    init._check_and_fix_dimension_mismatch = AsyncMock()
    init._repair_message_counts = AsyncMock()
    init._auto_rebuild_index_if_needed = AsyncMock()

    await init._complete_initialization()

    assert len(created_vec_dbs) == 2
    assert created_vec_dbs[1].db_path.endswith("livingmemory_graph_documents.db")
    assert created_vec_dbs[1].index_path.endswith("livingmemory_graph.index")
    assert init.memory_engine.graph_vector_db is init.graph_db
    assert init.memory_engine.config["graph_memory_enabled"] is True
    assert init.memory_engine.config["document_route_weight"] == 0.7
    assert init.memory_engine.config["graph_route_weight"] == 0.3
    assert init.memory_engine.config["cross_route_bonus"] == 0.12
    assert init.memory_engine.config["graph_expansion_limit"] == 12
    assert init.memory_engine.config["graph_max_topics"] == 4
    assert init.memory_engine.config["graph_max_participants"] == 5
    assert init.memory_engine.config["graph_max_facts"] == 6


@pytest.mark.asyncio
async def test_complete_initialization_skips_graph_db_when_disabled(
    monkeypatch, mock_context, tmp_path
):
    created_vec_dbs = []

    class DummyEmbeddingProvider:
        pass

    class DummyProvider:
        pass

    class FakeFaissVecDB:
        def __init__(self, db_path, index_path, embedding_provider):
            self.db_path = db_path
            self.index_path = index_path
            self.embedding_provider = embedding_provider
            created_vec_dbs.append(self)

        async def initialize(self):
            return None

    class FakeDBMigration:
        def __init__(self, db_path):
            self.db_path = db_path

    class FakeMemoryEngine:
        def __init__(
            self, db_path, faiss_db, graph_vector_db, llm_provider=None, config=None
        ):
            self.db_path = db_path
            self.faiss_db = faiss_db
            self.graph_vector_db = graph_vector_db
            self.llm_provider = llm_provider
            self.config = config or {}
            self.text_processor = Mock(async_init=AsyncMock())

        async def initialize(self):
            return None

    class FakeConversationStore:
        def __init__(self, db_path):
            self.db_path = db_path

        async def initialize(self):
            return None

        async def sync_message_counts(self):
            return []

    class FakeConversationManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeMemoryProcessor:
        def __init__(self, provider, context):
            self.provider = provider
            self.context = context

    class FakeIndexValidator:
        def __init__(self, db_path, db):
            self.db_path = db_path
            self.db = db

    class FakeDecayScheduler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def start(self):
            return None

    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.EmbeddingProvider",
        DummyEmbeddingProvider,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.Provider",
        DummyProvider,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.FaissVecDB",
        FakeFaissVecDB,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.DBMigration",
        FakeDBMigration,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.MemoryEngine",
        FakeMemoryEngine,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.ConversationStore",
        FakeConversationStore,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.ConversationManager",
        FakeConversationManager,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.MemoryProcessor",
        FakeMemoryProcessor,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.IndexValidator",
        FakeIndexValidator,
    )
    monkeypatch.setattr(
        "astrbot_plugin_livingmemory.core.plugin_initializer.DecayScheduler",
        FakeDecayScheduler,
    )

    init = PluginInitializer(
        mock_context,
        ConfigManager(
            {
                "migration_settings": {"auto_migrate": False},
                "importance_decay": {"decay_rate": 0},
                "forgetting_agent": {"auto_cleanup_enabled": False},
                "graph_memory": {"enabled": False},
            }
        ),
        str(tmp_path),
    )
    init.embedding_provider = DummyEmbeddingProvider()
    init.llm_provider = DummyProvider()
    init._check_and_fix_dimension_mismatch = AsyncMock()
    init._repair_message_counts = AsyncMock()
    init._auto_rebuild_index_if_needed = AsyncMock()

    await init._complete_initialization()

    assert len(created_vec_dbs) == 1
    assert init.graph_db is None
    assert init.memory_engine.graph_vector_db is None
    assert init.memory_engine.config["graph_memory_enabled"] is False
    init._check_and_fix_dimension_mismatch.assert_awaited_once()

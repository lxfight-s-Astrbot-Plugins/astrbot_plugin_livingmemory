"""
Tests for PluginInitializer state management and provider resolution.
"""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import astrbot_plugin_livingmemory.core.plugin_initializer as plugin_initializer_mod
import pytest
from astrbot_plugin_livingmemory.core.base.config_manager import ConfigManager
from astrbot_plugin_livingmemory.core.base.exceptions import InitializationError
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


def test_check_faiss_runtime_raises_actionable_error(monkeypatch, initializer):
    result = subprocess.CompletedProcess(
        args=[],
        returncode=-4,
        stdout="",
        stderr="Illegal instruction",
    )
    monkeypatch.setattr(
        plugin_initializer_mod.subprocess, "run", Mock(return_value=result)
    )

    with pytest.raises(InitializationError, match="CPU 或运行环境可能不兼容"):
        initializer._check_faiss_runtime()


def test_check_faiss_runtime_reports_binding_mismatch(monkeypatch, initializer):
    result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=(
            "NameError: name 'SuperKMeans' is not defined. "
            "Did you mean: 'SuperKmeans'?"
        ),
    )
    run = Mock(return_value=result)
    monkeypatch.setattr(plugin_initializer_mod.subprocess, "run", run)
    monkeypatch.setattr(
        plugin_initializer_mod.metadata, "version", Mock(return_value="1.14.2")
    )

    with pytest.raises(InitializationError) as exc_info:
        initializer._check_faiss_runtime()

    message = str(exc_info.value)
    assert "faiss-cpu 1.14.2" in message
    assert "不是 Embedding Provider 配置问题" in message
    assert "AstrBot Desktop" in message
    assert "1.14.3" in message
    assert run.call_count == 1


def test_check_faiss_runtime_falls_back_to_generic(monkeypatch, initializer):
    failed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="optimized import failed"
    )
    succeeded = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    run = Mock(side_effect=[failed, succeeded])
    monkeypatch.setattr(plugin_initializer_mod.subprocess, "run", run)
    monkeypatch.delenv("FAISS_OPT_LEVEL", raising=False)

    initializer._check_faiss_runtime()

    assert plugin_initializer_mod.os.environ["FAISS_OPT_LEVEL"] == "generic"
    assert run.call_count == 2
    assert run.call_args_list[1].kwargs["env"]["FAISS_OPT_LEVEL"] == "generic"


def test_requirements_do_not_pin_faiss_cpu():
    """faiss 由 AstrBot 核心提供，插件不应声明 faiss-cpu 依赖，避免版本冲突（见 #247）。"""
    requirements = (Path(__file__).parents[1] / "requirements.txt").read_text()

    pinned = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip().startswith("faiss-cpu")
    ]
    assert not pinned, f"requirements.txt 不应固定 faiss-cpu: {pinned}"


def test_load_faiss_vec_db_class_uses_patched_class(monkeypatch, initializer):
    class FakeFaissVecDB:
        pass

    monkeypatch.setattr(plugin_initializer_mod, "FaissVecDB", FakeFaissVecDB)

    assert initializer._load_faiss_vec_db_class() is FakeFaissVecDB


@pytest.mark.asyncio
async def test_startup_index_rebuild_runs_in_background(initializer):
    rebuild_started = asyncio.Event()
    allow_rebuild_to_finish = asyncio.Event()

    class _Validator:
        async def get_migration_status(self):
            return True, 120

        async def rebuild_indexes(self, memory_engine, progress_callback=None):
            del memory_engine
            rebuild_started.set()
            if progress_callback:
                await progress_callback(50, 120, "halfway")
            await allow_rebuild_to_finish.wait()
            return {
                "success": True,
                "processed": 120,
                "errors": 0,
                "total": 120,
                "partial": False,
                "message": "done",
            }

    initializer.index_validator = _Validator()
    initializer.memory_engine = SimpleNamespace(index_maintenance_status={})

    await initializer._auto_rebuild_index_if_needed()
    await asyncio.wait_for(rebuild_started.wait(), timeout=1)

    assert initializer.index_maintenance_status["state"] == "rebuilding"
    assert initializer.index_maintenance_status["current"] == 50
    assert initializer._index_maintenance_task is not None
    assert not initializer._index_maintenance_task.done()

    task = initializer._index_maintenance_task
    allow_rebuild_to_finish.set()
    await task

    assert initializer.index_maintenance_status["state"] == "ready"
    assert initializer.index_maintenance_status["current"] == 120
    assert initializer.memory_engine.index_maintenance_status["state"] == "ready"


@pytest.mark.asyncio
async def test_index_maintenance_reconciles_concurrent_writes_once(initializer):
    inconsistent = SimpleNamespace(
        is_consistent=False,
        needs_rebuild=True,
        reason="BM25索引缺失1条文档",
    )
    consistent = SimpleNamespace(
        is_consistent=True,
        needs_rebuild=False,
        reason="索引状态正常",
    )
    rebuild_indexes = AsyncMock(
        side_effect=[
            {
                "success": True,
                "processed": 100,
                "errors": 0,
                "total": 100,
                "partial": False,
                "message": "first pass",
            },
            {
                "success": True,
                "processed": 1,
                "errors": 0,
                "total": 101,
                "partial": False,
                "message": "reconciled",
            },
        ]
    )
    initializer.index_validator = SimpleNamespace(
        rebuild_indexes=rebuild_indexes,
        check_consistency=AsyncMock(side_effect=[inconsistent, consistent]),
    )
    initializer.memory_engine = SimpleNamespace(index_maintenance_status={})

    await initializer._run_scheduled_index_rebuild("repair", 100)

    assert rebuild_indexes.await_count == 2
    assert initializer.index_maintenance_status["state"] == "ready"
    assert "reconciliation" in initializer.index_maintenance_status["result"]


@pytest.mark.asyncio
async def test_provider_change_rebuilds_document_and_graph_indexes(initializer):
    consistent = SimpleNamespace(
        is_consistent=True,
        needs_rebuild=False,
        reason="索引状态正常",
        documents_count=3,
    )
    initializer.index_validator = SimpleNamespace(
        provider_fingerprint_changed=AsyncMock(return_value=True),
        check_consistency=AsyncMock(return_value=consistent),
        rebuild_indexes=AsyncMock(
            return_value={
                "success": True,
                "processed": 3,
                "errors": 0,
                "total": 3,
                "partial": False,
                "message": "done",
            }
        ),
    )
    initializer.memory_engine = SimpleNamespace(
        index_maintenance_status={},
        rebuild_graph_index=AsyncMock(return_value={"rebuilt": 3, "skipped": 0}),
    )

    await initializer._run_index_maintenance()

    initializer.memory_engine.rebuild_graph_index.assert_awaited_once()
    assert initializer.index_maintenance_status["state"] == "ready"
    assert initializer.index_maintenance_status["result"]["graph_rebuild"] == {
        "rebuilt": 3,
        "skipped": 0,
    }


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
        def __init__(self, context=None, llm_provider=None, **kwargs):
            self.context = context
            self.llm_provider = llm_provider
            self.config = kwargs.get("config", {})

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
                    "atom_enabled": False,
                    "atom_maintenance_interval_hours": 12.0,
                    "atom_forget_delay_days": 3.0,
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
    assert init.memory_engine.config["atom_enabled"] is False
    assert init.memory_engine.config["atom_maintenance_interval_hours"] == 12.0
    assert init.memory_engine.config["atom_forget_delay_days"] == 3.0
    assert init.memory_processor.config.get("atom_enabled") is False


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
        def __init__(self, context=None, llm_provider=None, **kwargs):
            self.context = context
            self.llm_provider = llm_provider

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


@pytest.mark.asyncio
async def test_initialize_failure_starts_retry_not_permanent(
    monkeypatch, mock_context, tmp_path
):
    """瞬态初始化失败不应永久禁用插件，而是清理后转交后台重试。"""
    init = PluginInitializer(mock_context, ConfigManager(), str(tmp_path))
    init._wait_for_providers_non_blocking = AsyncMock(return_value=True)
    init._complete_initialization = AsyncMock(
        side_effect=InitializationError("transient boom")
    )
    init._teardown_partial_init = AsyncMock()
    start_retry = Mock()
    monkeypatch.setattr(init, "_start_retry_task_if_needed", start_retry)

    ok = await init.initialize()

    assert ok is False
    assert init.is_failed is False
    assert init.error_message == "transient boom"
    init._teardown_partial_init.assert_awaited_once()
    start_retry.assert_called_once()


@pytest.mark.asyncio
async def test_retry_initialization_recovers_after_transient_failure(
    monkeypatch, mock_context, tmp_path
):
    """首次完整初始化失败后，重试任务应继续尝试并最终成功。"""
    init = PluginInitializer(mock_context, ConfigManager(), str(tmp_path))
    init.embedding_provider = Mock()
    init.llm_provider = Mock()
    init._max_provider_attempts = 3
    init._initialize_providers = Mock()
    init._teardown_partial_init = AsyncMock()
    monkeypatch.setattr(plugin_initializer_mod.asyncio, "sleep", AsyncMock())

    attempts = 0

    async def fake_complete():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise InitializationError("transient")
        init._initialization_complete = True

    init._complete_initialization = fake_complete

    await init._retry_initialization()

    assert attempts == 2
    assert init.is_initialized is True
    assert init.is_failed is False
    init._teardown_partial_init.assert_awaited_once()


@pytest.mark.asyncio
async def test_teardown_partial_init_closes_resources(initializer):
    """清理半初始化资源时应关闭已创建的组件并置空引用。"""
    engine = Mock()
    engine.close = AsyncMock()
    db = Mock()
    db.close = AsyncMock()
    graph_db = Mock()
    graph_db.close = AsyncMock()
    store = Mock()
    store.close = AsyncMock()
    conv_mgr = Mock()
    conv_mgr.store = store
    scheduler = Mock()
    scheduler.stop = AsyncMock()

    initializer.memory_engine = engine
    initializer.db = db
    initializer.graph_db = graph_db
    initializer.conversation_manager = conv_mgr
    initializer.decay_scheduler = scheduler

    await initializer._teardown_partial_init()

    scheduler.stop.assert_awaited_once()
    store.close.assert_awaited_once()
    engine.close.assert_awaited_once()
    db.close.assert_awaited_once()
    # graph_db 由 memory_engine.close() 关闭（graph_vector_db），不应重复关闭。
    graph_db.close.assert_not_called()
    assert initializer.memory_engine is None
    assert initializer.db is None
    assert initializer.graph_db is None
    assert initializer.conversation_manager is None
    assert initializer.decay_scheduler is None


@pytest.mark.asyncio
async def test_teardown_partial_init_closes_orphan_graph_db(initializer):
    """memory_engine 未创建时，graph_db 应被显式关闭。"""
    graph_db = Mock()
    graph_db.close = AsyncMock()
    db = Mock()
    db.close = AsyncMock()

    initializer.graph_db = graph_db
    initializer.db = db

    await initializer._teardown_partial_init()

    graph_db.close.assert_awaited_once()
    db.close.assert_awaited_once()
    assert initializer.graph_db is None
    assert initializer.db is None

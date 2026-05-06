<div align="center">

[中文](API.md) | [English](API_en.md) | [Русский](API_ru.md)

</div>

# LivingMemory API Documentation

**Version**: v2.2.10
**Last Updated**: 2026-04-28

---

## Table of Contents

1. [Core Modules](#core-modules)
2. [Configuration Management](#configuration-management)
3. [Exception Handling](#exception-handling)
4. [Event Handling](#event-handling)
5. [Command Handling](#command-handling)
6. [Agent Tools](#agent-tools)

---

## Core Modules

### PluginInitializer

The plugin initializer, responsible for the plugin's initialization logic.

#### Constructor

```python
PluginInitializer(context: Context, config_manager: ConfigManager)
```

**Parameters**:
- `context`: AstrBot context object
- `config_manager`: Configuration manager instance

#### Methods

##### `async initialize() -> bool`

Execute plugin initialization.

**Returns**: Whether initialization was successful

**Example**:
```python
initializer = PluginInitializer(context, config_manager)
success = await initializer.initialize()
```

##### `async ensure_initialized(timeout: float = 30.0) -> bool`

Ensure the plugin is initialized, waiting if not yet initialized.

**Parameters**:
- `timeout`: Timeout in seconds

**Returns**: Whether initialization was successful

##### Properties

- `is_initialized: bool` - Whether initialization is complete
- `is_failed: bool` - Whether initialization has failed
- `error_message: str | None` - Error message

---

## Configuration Management

### ConfigManager

The configuration manager, centralized management of plugin configuration.

#### Constructor

```python
ConfigManager(user_config: dict[str, Any] | None = None)
```

**Parameters**:
- `user_config`: User-provided configuration dictionary (optional)

#### Methods

##### `get(key: str, default: Any = None) -> Any`

Get a configuration item, supports dot-separated nested keys.

**Parameters**:
- `key`: Configuration key (e.g., "provider_settings.llm_provider_id")
- `default`: Default value

**Returns**: Configuration value

**Example**:
```python
config = ConfigManager()
llm_id = config.get("provider_settings.llm_provider_id", "default_llm")
```

##### `get_section(section: str) -> dict[str, Any]`

Get a configuration section.

**Parameters**:
- `section`: Section name

**Returns**: Section dictionary

##### `get_all() -> dict[str, Any]`

Get all configuration.

**Returns**: Complete configuration dictionary

#### Properties

- `provider_settings: dict` - Provider settings
- `webui_settings: dict` - WebUI settings
- `session_manager: dict` - Session manager configuration
- `recall_engine: dict` - Recall engine configuration
- `reflection_engine: dict` - Reflection engine configuration
- `filtering_settings: dict` - Filtering settings

---

## Exception Handling

### Exception Class Hierarchy

```
LivingMemoryException (Base)
├── InitializationError (Initialization error)
├── ProviderNotReadyError (Provider not ready)
├── DatabaseError (Database error)
├── RetrievalError (Retrieval error)
├── MemoryProcessingError (Memory processing error)
├── ConfigurationError (Configuration error)
└── ValidationError (Validation error)
```

### LivingMemoryException

Base class for all custom exceptions.

#### Constructor

```python
LivingMemoryException(message: str, error_code: str | None = None)
```

**Parameters**:
- `message`: Error message
- `error_code`: Error code (optional)

#### Properties

- `message: str` - Error message
- `error_code: str` - Error code

**Example**:
```python
try:
    # some operation
    pass
except LivingMemoryException as e:
    print(f"Error: {e.message}, code: {e.error_code}")
```

---

## Event Handling

### EventHandler

Event handler, responsible for processing AstrBot event hooks.

#### Constructor

```python
EventHandler(
    context: Any,
    config_manager: ConfigManager,
    memory_engine: MemoryEngine,
    memory_processor: MemoryProcessor,
    conversation_manager: ConversationManager,
)
```

#### Methods

##### `async handle_all_group_messages(event: AstrMessageEvent)`

Capture all group chat messages for memory storage.

**Parameters**:
- `event`: AstrBot message event

##### `async handle_memory_recall(event: AstrMessageEvent, req: ProviderRequest)`

Before LLM request, query and inject long-term memory.

**Parameters**:
- `event`: AstrBot message event
- `req`: Provider request object

##### `async handle_memory_reflection(event: AstrMessageEvent, resp: LLMResponse)`

After LLM response, check whether reflection and memory storage are needed.

**Parameters**:
- `event`: AstrBot message event
- `resp`: LLM response object

---

## Command Handling

### CommandHandler

Command handler, responsible for processing plugin commands.

#### Constructor

```python
CommandHandler(
    config_manager: ConfigManager,
    memory_engine: MemoryEngine | None,
    conversation_manager: ConversationManager | None,
    index_validator: IndexValidator | None,
    webui_server=None,
    initialization_status_callback=None,
)
```

#### Methods

All command handling methods return `AsyncGenerator[str, None]` for streaming message output.

##### `async handle_status(event: AstrMessageEvent)`

Handle `/lmem status` command, display memory system status.

**Example**:
```python
async for message in command_handler.handle_status(event):
    print(message)
```

##### `async handle_search(event: AstrMessageEvent, query: str, k: int = 5)`

Handle `/lmem search` command, search memories.

**Parameters**:
- `event`: AstrBot message event
- `query`: Search query
- `k`: Number of results to return

##### `async handle_summarize(event: AstrMessageEvent)`

Handle `/lmem summarize` command, trigger immediate memory summarization for the current session.

**Parameters**:
- `event`: AstrBot message event

##### `async handle_rebuild_graph(event: AstrMessageEvent)`

Handle `/lmem rebuild-graph` command, rebuild graph memory indexes (backfill graph data for old memories).

**Parameters**:
- `event`: AstrBot message event

##### `async handle_cleanup(event: AstrMessageEvent, mode: str = "preview")`

Handle `/lmem cleanup [preview|exec]` command, clean up memory injection fragments from historical messages.

**Parameters**:
- `event`: AstrBot message event
- `mode`: Mode, `preview` (default) or `exec`

---

## Agent Tools

### MemorySearchTool

A long-term memory recall tool for active use by the tool loop / agent.

#### Tool Name

`recall_long_term_memory`

#### Behavior

- Uses keywords chosen by the agent itself to recall long-term memory, rather than relying only on the current round's message text.
- Automatically reuses the current configuration's session isolation and persona isolation settings.
- Returns a raw memory list for the agent to judge which results should be included in the final answer.
- Retrieval results enter the tool context and do not go through the `handle_memory_recall()` prompt injection path.

#### Input Parameters

- `query: str` - Recall keywords. Prefer topic, entity name, preference, agreement, or historical event phrases with high information content.
- `k: int = 5` - Maximum number of memory items to return for one recall. Upper-layer implementation will enforce range limits.

#### Return Result

Returns JSON text containing the following fields:

- `query`: The actual executed query
- `applied_filters.session_filtered`: Whether filtered by current session
- `applied_filters.persona_filtered`: Whether filtered by current persona
- `count`: Number of returned results
- `results`: Raw memory list

Each memory result contains:

- `id`
- `content`
- `score`
- `importance`
- `session_id`
- `persona_id`
- `create_time`
- `last_access_time`

##### `async handle_forget(event: AstrMessageEvent, doc_id: int)`

Handle `/lmem forget` command, delete a specific memory.

**Parameters**:
- `event`: AstrBot message event
- `doc_id`: Memory ID

##### `async handle_rebuild_index(event: AstrMessageEvent)`

Handle `/lmem rebuild-index` command, rebuild indexes.

##### `async handle_webui(event: AstrMessageEvent)`

Handle `/lmem webui` command, display WebUI access information.

##### `async handle_reset(event: AstrMessageEvent)`

Handle `/lmem reset` command, reset the current session's memory context.

##### `async handle_help(event: AstrMessageEvent)`

Handle `/lmem help` command, display help information.

---

## Utility Components

### FakeToolCallFormatter (`core/utils/`)

Formats memories as a fake LLM tool call message, compatible with Agent / Tool Loop mode.

#### `format_memories_for_fake_tool_call(memories: list, max_token_budget: int) -> list`

**Parameters**:
- `memories`: Memory list (containing content, score, importance, etc.)
- `max_token_budget`: Maximum token budget; automatically truncates if exceeded

**Returns**: A list of messages with `tool_calls` and `tool` roles, ready for direct injection into the AstrBot conversation context.

**Characteristics**:
- Uses a fixed prefix `fake_recall_` as the call_id, making it easy for `EventHandler` to automatically clean up before each new turn
- Memory content is placed in the tool return as JSON, avoiding pollution of user-visible text

---

### AutoBackup (`storage/`)

Scheduled automatic backup subsystem.

#### `async run_backup_cycle()`

Executes one backup cycle: checks if backup time is due → creates a `.db` backup → cleans up expired files.

**Configuration** (via `ConfigManager`):
- `backup.enabled`: Whether auto-backup is enabled
- `backup.interval_hours`: Backup interval in hours
- `backup.retention_days`: Retention period in days
- `backup.directory`: Backup directory path

**Returns**: `{"success": bool, "backup_path": str | None, "error": str | None}`

---

## Usage Examples

### Complete Plugin Usage Example

```python
from astrbot.api.star import Context
from core.config_manager import ConfigManager
from core.plugin_initializer import PluginInitializer
from core.event_handler import EventHandler
from core.command_handler import CommandHandler

# 1. Create configuration manager
config = {
    "provider_settings": {
        "llm_provider_id": "openai_gpt4"
    }
}
config_manager = ConfigManager(config)

# 2. Create plugin initializer
context = get_astrbot_context()  # Get AstrBot context
initializer = PluginInitializer(context, config_manager)

# 3. Initialize plugin
success = await initializer.initialize()
if not success:
    print(f"Initialization failed: {initializer.error_message}")
    return

# 4. Create event handler
event_handler = EventHandler(
    context=context,
    config_manager=config_manager,
    memory_engine=initializer.memory_engine,
    memory_processor=initializer.memory_processor,
    conversation_manager=initializer.conversation_manager,
)

# 5. Create command handler
command_handler = CommandHandler(
    config_manager=config_manager,
    memory_engine=initializer.memory_engine,
    conversation_manager=initializer.conversation_manager,
    index_validator=initializer.index_validator,
)

# 6. Handle events
await event_handler.handle_memory_recall(event, req)

# 7. Handle commands
async for message in command_handler.handle_status(event):
    print(message)
```

---

## Error Handling Examples

```python
from core.exceptions import (
    InitializationError,
    ProviderNotReadyError,
    DatabaseError,
)

try:
    # Initialize plugin
    await initializer.initialize()
except ProviderNotReadyError as e:
    print(f"Provider not ready: {e.message}")
    # Handle provider not ready situation
except InitializationError as e:
    print(f"Initialization failed: {e.message}")
    # Handle initialization failure situation
except DatabaseError as e:
    print(f"Database error: {e.message}")
    # Handle database error situation
```

---

## Configuration Example

### Complete Configuration Example

```json
{
  "provider_settings": {
    "embedding_provider_id": "openai_embedding",
    "llm_provider_id": "openai_gpt4"
  },
  "webui_settings": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8080,
    "access_password": "your_password",
    "session_timeout": 3600
  },
  "session_manager": {
    "max_sessions": 100,
    "session_ttl": 3600,
    "context_window_size": 50
  },
  "recall_engine": {
    "top_k": 5,
    "importance_weight": 1.0,
    "fallback_to_vector": true,
    "injection_method": "user_message_before",
    "auto_remove_injected": true
  },
  "reflection_engine": {
    "summary_trigger_rounds": 10
  }
}
```

---

## More Information

- [Architecture Documentation](ARCHITECTURE.md)
- [Developer Guide](DEVELOPMENT.md)
- [GitHub Repository](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)

---

**Document Version**: v2.2.10
**Last Updated**: 2026-04-28

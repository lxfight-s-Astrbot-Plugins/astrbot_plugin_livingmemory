# LivingMemory API 文档

**版本**: v2.0.0
**更新日期**: 2025-12-17

---

## 目录

1. [核心模块](#核心模块)
2. [配置管理](#配置管理)
3. [异常处理](#异常处理)
4. [事件处理](#事件处理)
5. [命令处理](#命令处理)

---

## 核心模块

### PluginInitializer

插件初始化器，负责插件的初始化逻辑。

#### 构造函数

```python
PluginInitializer(context: Context, config_manager: ConfigManager)
```

**参数**:
- `context`: AstrBot上下文对象
- `config_manager`: 配置管理器实例

#### 方法

##### `async initialize() -> bool`

执行插件初始化。

**返回**: 是否初始化成功

**示例**:
```python
initializer = PluginInitializer(context, config_manager)
success = await initializer.initialize()
```

##### `async ensure_initialized(timeout: float = 30.0) -> bool`

确保插件已初始化，如果未初始化则等待。

**参数**:
- `timeout`: 超时时间（秒）

**返回**: 是否初始化成功

##### 属性

- `is_initialized: bool` - 是否已初始化
- `is_failed: bool` - 是否初始化失败
- `error_message: str | None` - 错误消息

---

## 配置管理

### ConfigManager

配置管理器，集中管理插件配置。

#### 构造函数

```python
ConfigManager(user_config: dict[str, Any] | None = None)
```

**参数**:
- `user_config`: 用户提供的配置字典（可选）

#### 方法

##### `get(key: str, default: Any = None) -> Any`

获取配置项，支持点号分隔的嵌套键。

**参数**:
- `key`: 配置键（如 "provider_settings.llm_provider_id"）
- `default`: 默认值

**返回**: 配置值

**示例**:
```python
config = ConfigManager()
llm_id = config.get("provider_settings.llm_provider_id", "default_llm")
```

##### `get_section(section: str) -> dict[str, Any]`

获取配置节。

**参数**:
- `section`: 配置节名称

**返回**: 配置节字典

##### `get_all() -> dict[str, Any]`

获取所有配置。

**返回**: 完整配置字典

#### 属性

- `provider_settings: dict` - Provider设置
- `webui_settings: dict` - WebUI设置
- `session_manager: dict` - 会话管理器配置
- `recall_engine: dict` - 召回引擎配置
- `reflection_engine: dict` - 反思引擎配置
- `filtering_settings: dict` - 过滤设置

---

## 异常处理

### 异常类层次结构

```
LivingMemoryException (基类)
├── InitializationError (初始化错误)
├── ProviderNotReadyError (Provider未就绪)
├── DatabaseError (数据库错误)
├── RetrievalError (检索错误)
├── MemoryProcessingError (记忆处理错误)
├── ConfigurationError (配置错误)
└── ValidationError (验证错误)
```

### LivingMemoryException

所有自定义异常的基类。

#### 构造函数

```python
LivingMemoryException(message: str, error_code: str | None = None)
```

**参数**:
- `message`: 错误消息
- `error_code`: 错误码（可选）

#### 属性

- `message: str` - 错误消息
- `error_code: str` - 错误码

**示例**:
```python
try:
    # 某些操作
    pass
except LivingMemoryException as e:
    print(f"错误: {e.message}, 错误码: {e.error_code}")
```

---

## 事件处理

### EventHandler

事件处理器，负责处理AstrBot事件钩子。

#### 构造函数

```python
EventHandler(
    context: Any,
    config_manager: ConfigManager,
    memory_engine: MemoryEngine,
    memory_processor: MemoryProcessor,
    conversation_manager: ConversationManager,
)
```

#### 方法

##### `async handle_all_group_messages(event: AstrMessageEvent)`

捕获所有群聊消息用于记忆存储。

**参数**:
- `event`: AstrBot消息事件

##### `async handle_memory_recall(event: AstrMessageEvent, req: ProviderRequest)`

在LLM请求前，查询并注入长期记忆。

**参数**:
- `event`: AstrBot消息事件
- `req`: Provider请求对象

##### `async handle_memory_reflection(event: AstrMessageEvent, resp: LLMResponse)`

在LLM响应后，检查是否需要进行反思和记忆存储。

**参数**:
- `event`: AstrBot消息事件
- `resp`: LLM响应对象

---

## 命令处理

### CommandHandler

命令处理器，负责处理插件命令。

#### 构造函数

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

#### 方法

所有命令处理方法都返回 `AsyncGenerator[str, None]`，用于流式返回消息。

##### `async handle_status(event: AstrMessageEvent)`

处理 `/lmem status` 命令，显示记忆系统状态。

**示例**:
```python
async for message in command_handler.handle_status(event):
    print(message)
```

##### `async handle_search(event: AstrMessageEvent, query: str, k: int = 5)`

处理 `/lmem search` 命令，搜索记忆。

**参数**:
- `event`: AstrBot消息事件
- `query`: 搜索查询
- `k`: 返回结果数量

##### `async handle_forget(event: AstrMessageEvent, doc_id: int)`

处理 `/lmem forget` 命令，删除指定记忆。

**参数**:
- `event`: AstrBot消息事件
- `doc_id`: 记忆ID

##### `async handle_rebuild_index(event: AstrMessageEvent)`

处理 `/lmem rebuild-index` 命令，重建索引。

##### `async handle_webui(event: AstrMessageEvent)`

处理 `/lmem webui` 命令，显示WebUI访问信息。

##### `async handle_reset(event: AstrMessageEvent)`

处理 `/lmem reset` 命令，重置当前会话的记忆上下文。

##### `async handle_help(event: AstrMessageEvent)`

处理 `/lmem help` 命令，显示帮助信息。

---

## 使用示例

### 完整的插件使用示例

```python
from astrbot.api.star import Context
from core.config_manager import ConfigManager
from core.plugin_initializer import PluginInitializer
from core.event_handler import EventHandler
from core.command_handler import CommandHandler

# 1. 创建配置管理器
config = {
    "provider_settings": {
        "llm_provider_id": "openai_gpt4"
    }
}
config_manager = ConfigManager(config)

# 2. 创建插件初始化器
context = get_astrbot_context()  # 获取AstrBot上下文
initializer = PluginInitializer(context, config_manager)

# 3. 初始化插件
success = await initializer.initialize()
if not success:
    print(f"初始化失败: {initializer.error_message}")
    return

# 4. 创建事件处理器
event_handler = EventHandler(
    context=context,
    config_manager=config_manager,
    memory_engine=initializer.memory_engine,
    memory_processor=initializer.memory_processor,
    conversation_manager=initializer.conversation_manager,
)

# 5. 创建命令处理器
command_handler = CommandHandler(
    config_manager=config_manager,
    memory_engine=initializer.memory_engine,
    conversation_manager=initializer.conversation_manager,
    index_validator=initializer.index_validator,
)

# 6. 处理事件
await event_handler.handle_memory_recall(event, req)

# 7. 处理命令
async for message in command_handler.handle_status(event):
    print(message)
```

---

## 错误处理示例

```python
from core.exceptions import (
    InitializationError,
    ProviderNotReadyError,
    DatabaseError,
)

try:
    # 初始化插件
    await initializer.initialize()
except ProviderNotReadyError as e:
    print(f"Provider未就绪: {e.message}")
    # 处理Provider未就绪的情况
except InitializationError as e:
    print(f"初始化失败: {e.message}")
    # 处理初始化失败的情况
except DatabaseError as e:
    print(f"数据库错误: {e.message}")
    # 处理数据库错误的情况
```

---

## 配置示例

### 完整配置示例

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
    "summary_trigger_rounds": 10,
    "save_original_conversation": false
  }
}
```

---

## 更多信息

- [架构文档](ARCHITECTURE.md)
- [开发者指南](DEVELOPMENT.md)
- [GitHub仓库](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)

---

**文档版本**: v2.0.0
**最后更新**: 2025-12-17

<div align="center">

[中文](API.md) | [English](API_en.md) | [Русский](API_ru.md)

</div>

# LivingMemory API-документация

**Версия**: v2.2.10
**Дата обновления**: 2026-04-28

---

## Содержание

1. [Основные модули](#основные-модули)
2. [Управление конфигурацией](#управление-конфигурацией)
3. [Обработка исключений](#обработка-исключений)
4. [Обработка событий](#обработка-событий)
5. [Обработка команд](#обработка-команд)
6. [Инструменты Agent](#инструменты-agent)

---

## Основные модули

### PluginInitializer

Инициализатор плагина, отвечает за логику инициализации.

#### Конструктор

```python
PluginInitializer(context: Context, config_manager: ConfigManager)
```

**Параметры**:
- `context`: Объект контекста AstrBot
- `config_manager`: Экземпляр менеджера конфигурации

#### Методы

##### `async initialize() -> bool`

Выполнить инициализацию плагина.

**Возвращает**: Успешна ли инициализация

**Пример**:
```python
initializer = PluginInitializer(context, config_manager)
success = await initializer.initialize()
```

##### `async ensure_initialized(timeout: float = 30.0) -> bool`

Убедиться, что плагин инициализирован, ожидать при необходимости.

**Параметры**:
- `timeout`: Таймаут в секундах

**Возвращает**: Успешна ли инициализация

##### Свойства

- `is_initialized: bool` — Инициализация завершена
- `is_failed: bool` — Инициализация не удалась
- `error_message: str | None` — Сообщение об ошибке

---

## Управление конфигурацией

### ConfigManager

Менеджер конфигурации, централизованное управление конфигурацией плагина.

#### Конструктор

```python
ConfigManager(user_config: dict[str, Any] | None = None)
```

**Параметры**:
- `user_config`: Конфигурационный словарь, предоставленный пользователем (опционально)

#### Методы

##### `get(key: str, default: Any = None) -> Any`

Получить элемент конфигурации, поддерживает вложенные ключи через точку.

**Параметры**:
- `key`: Ключ конфигурации (например, "provider_settings.llm_provider_id")
- `default`: Значение по умолчанию

**Возвращает**: Значение конфигурации

**Пример**:
```python
config = ConfigManager()
llm_id = config.get("provider_settings.llm_provider_id", "default_llm")
```

##### `get_section(section: str) -> dict[str, Any]`

Получить раздел конфигурации.

**Параметры**:
- `section`: Имя раздела

**Возвращает**: Словарь раздела

##### `get_all() -> dict[str, Any]`

Получить всю конфигурацию.

**Возвращает**: Полный конфигурационный словарь

#### Свойства

- `provider_settings: dict` — Настройки провайдера
- `webui_settings: dict` — Настройки WebUI
- `session_manager: dict` — Конфигурация менеджера сессий
- `recall_engine: dict` — Конфигурация движка восстановления памяти
- `reflection_engine: dict` — Конфигурация движка рефлексии
- `filtering_settings: dict` — Настройки фильтрации

---

## Обработка исключений

### Иерархия классов исключений

```
LivingMemoryException (Базовый)
├── InitializationError (Ошибка инициализации)
├── ProviderNotReadyError (Провайдер не готов)
├── DatabaseError (Ошибка базы данных)
├── RetrievalError (Ошибка поиска)
├── MemoryProcessingError (Ошибка обработки памяти)
├── ConfigurationError (Ошибка конфигурации)
└── ValidationError (Ошибка валидации)
```

### LivingMemoryException

Базовый класс для всех пользовательских исключений.

#### Конструктор

```python
LivingMemoryException(message: str, error_code: str | None = None)
```

**Параметры**:
- `message`: Сообщение об ошибке
- `error_code`: Код ошибки (опционально)

#### Свойства

- `message: str` — Сообщение об ошибке
- `error_code: str` — Код ошибки

**Пример**:
```python
try:
    # какая-либо операция
    pass
except LivingMemoryException as e:
    print(f"Ошибка: {e.message}, код: {e.error_code}")
```

---

## Обработка событий

### EventHandler

Обработчик событий, отвечает за обработку хуков событий AstrBot.

#### Конструктор

```python
EventHandler(
    context: Any,
    config_manager: ConfigManager,
    memory_engine: MemoryEngine,
    memory_processor: MemoryProcessor,
    conversation_manager: ConversationManager,
)
```

#### Методы

##### `async handle_all_group_messages(event: AstrMessageEvent)`

Захватывать все сообщения группового чата для хранения в памяти.

**Параметры**:
- `event`: Событие сообщения AstrBot

##### `async handle_memory_recall(event: AstrMessageEvent, req: ProviderRequest)`

Перед запросом к LLM выполнить поиск и инъекцию долгосрочной памяти.

**Параметры**:
- `event`: Событие сообщения AstrBot
- `req`: Объект запроса провайдера

##### `async handle_memory_reflection(event: AstrMessageEvent, resp: LLMResponse)`

После ответа LLM проверить, требуется ли рефлексия и сохранение памяти.

**Параметры**:
- `event`: Событие сообщения AstrBot
- `resp`: Объект ответа LLM

---

## Обработка команд

### CommandHandler

Обработчик команд, отвечает за обработку команд плагина.

#### Конструктор

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

#### Методы

Все методы обработки команд возвращают `AsyncGenerator[str, None]` для потоковой передачи сообщений.

##### `async handle_status(event: AstrMessageEvent)`

Обработать команду `/lmem status`, отобразить состояние системы памяти.

**Пример**:
```python
async for message in command_handler.handle_status(event):
    print(message)
```

##### `async handle_search(event: AstrMessageEvent, query: str, k: int = 5)`

Обработать команду `/lmem search`, выполнить поиск воспоминаний.

**Параметры**:
- `event`: Событие сообщения AstrBot
- `query`: Поисковый запрос
- `k`: Количество возвращаемых результатов

##### `async handle_summarize(event: AstrMessageEvent)`

Обработать команду `/lmem summarize`, немедленно запустить суммаризацию памяти для текущей сессии.

**Параметры**:
- `event`: Событие сообщения AstrBot

##### `async handle_rebuild_graph(event: AstrMessageEvent)`

Обработать команду `/lmem rebuild-graph`, перестроить графовые индексы памяти (заполнение графовых данных для старых воспоминаний).

**Параметры**:
- `event`: Событие сообщения AstrBot

##### `async handle_cleanup(event: AstrMessageEvent, mode: str = "preview")`

Обработать команду `/lmem cleanup [preview|exec]`, очистить вставленные фрагменты памяти из истории сообщений.

**Параметры**:
- `event`: Событие сообщения AstrBot
- `mode`: Режим, `preview` (предварительный просмотр, по умолчанию) или `exec` (выполнение)

---

## Инструменты Agent

### MemorySearchTool

Инструмент проактивного восстановления долгосрочной памяти для режима tool loop / agent.

#### Название инструмента

`recall_long_term_memory`

#### Поведение

- Agent сам выбирает ключевые слова для восстановления долгосрочной памяти, а не полагается только на текст сообщения текущего раунда.
- Автоматически повторно использует текущие настройки изоляции сессий и персон.
- Возвращает исходный список воспоминаний, чтобы agent сам оценил, какие результаты стоит включить в итоговый ответ.
- Результаты поиска попадают в контекст инструмента и не проходят повторно через цепочку инъекции промпта памяти `handle_memory_recall()`.

#### Входные параметры

- `query: str` — Ключевые слова для восстановления. Отдавайте предпочтение темам, именам сущностей, предпочтениям, договорённостям или историческим событиям — фразам с высокой информативностью.
- `k: int = 5` — Максимальное количество элементов памяти, возвращаемых за один вызов. Реализация верхнего уровня применит ограничение диапазона.

#### Возвращаемый результат

Возвращает JSON-текст, содержащий следующие поля:

- `query`: Фактически выполненный запрос
- `applied_filters.session_filtered`: Фильтровался ли по текущей сессии
- `applied_filters.persona_filtered`: Фильтровался ли по текущей персоне
- `count`: Количество возвращённых результатов
- `results`: Исходный список воспоминаний

Каждый результат воспоминания содержит:

- `id`
- `content`
- `score`
- `importance`
- `session_id`
- `persona_id`
- `create_time`
- `last_access_time`

##### `async handle_forget(event: AstrMessageEvent, doc_id: int)`

Обработать команду `/lmem forget`, удалить конкретное воспоминание.

**Параметры**:
- `event`: Событие сообщения AstrBot
- `doc_id`: ID воспоминания

##### `async handle_rebuild_index(event: AstrMessageEvent)`

Обработать команду `/lmem rebuild-index`, перестроить индексы.

##### `async handle_webui(event: AstrMessageEvent)`

Обработать команду `/lmem webui`, отобразить информацию о доступе к WebUI.

##### `async handle_reset(event: AstrMessageEvent)`

Обработать команду `/lmem reset`, сбросить контекст памяти текущей сессии.

##### `async handle_help(event: AstrMessageEvent)`

Обработать команду `/lmem help`, отобразить справочную информацию.

---

## Вспомогательные компоненты

### FakeToolCallFormatter (`core/utils/`)

Форматирует воспоминания как поддельное сообщение о вызове LLM-инструмента, совместимое с режимом Agent / Tool Loop.

#### `format_memories_for_fake_tool_call(memories: list, max_token_budget: int) -> list`

**Параметры**:
- `memories`: Список воспоминаний (содержит content, score, importance и др.)
- `max_token_budget`: Максимальный бюджет токенов; при превышении автоматически обрезается

**Возвращает**: Список сообщений с ролями `tool_calls` и `tool`, готовых к прямой инъекции в контекст диалога AstrBot.

**Особенности**:
- Использует фиксированный префикс `fake_recall_` в качестве call_id, что позволяет `EventHandler` автоматически очищать перед каждым новым ходом
- Содержимое воспоминаний помещается в ответ инструмента в формате JSON, не загрязняя видимый пользователю текст

---

### AutoBackup (`storage/`)

Подсистема автоматического резервного копирования по расписанию.

#### `async run_backup_cycle()`

Выполняет один цикл резервного копирования: проверяет, наступило ли время → создаёт `.db` бэкап → удаляет устаревшие файлы.

**Параметры конфигурации** (через `ConfigManager`):
- `backup.enabled`: Включено ли автоматическое резервное копирование
- `backup.interval_hours`: Интервал бэкапа в часах
- `backup.retention_days`: Срок хранения в днях
- `backup.directory`: Путь к каталогу бэкапов

**Возвращает**: `{"success": bool, "backup_path": str | None, "error": str | None}`

---

## Примеры использования

### Пример полного использования плагина

```python
from astrbot.api.star import Context
from core.config_manager import ConfigManager
from core.plugin_initializer import PluginInitializer
from core.event_handler import EventHandler
from core.command_handler import CommandHandler

# 1. Создать менеджер конфигурации
config = {
    "provider_settings": {
        "llm_provider_id": "openai_gpt4"
    }
}
config_manager = ConfigManager(config)

# 2. Создать инициализатор плагина
context = get_astrbot_context()  # Получить контекст AstrBot
initializer = PluginInitializer(context, config_manager)

# 3. Инициализировать плагин
success = await initializer.initialize()
if not success:
    print(f"Инициализация не удалась: {initializer.error_message}")
    return

# 4. Создать обработчик событий
event_handler = EventHandler(
    context=context,
    config_manager=config_manager,
    memory_engine=initializer.memory_engine,
    memory_processor=initializer.memory_processor,
    conversation_manager=initializer.conversation_manager,
)

# 5. Создать обработчик команд
command_handler = CommandHandler(
    config_manager=config_manager,
    memory_engine=initializer.memory_engine,
    conversation_manager=initializer.conversation_manager,
    index_validator=initializer.index_validator,
)

# 6. Обработать события
await event_handler.handle_memory_recall(event, req)

# 7. Обработать команды
async for message in command_handler.handle_status(event):
    print(message)
```

---

## Примеры обработки ошибок

```python
from core.exceptions import (
    InitializationError,
    ProviderNotReadyError,
    DatabaseError,
)

try:
    # Инициализировать плагин
    await initializer.initialize()
except ProviderNotReadyError as e:
    print(f"Провайдер не готов: {e.message}")
    # Обработать ситуацию «провайдер не готов»
except InitializationError as e:
    print(f"Инициализация не удалась: {e.message}")
    # Обработать ситуацию сбоя инициализации
except DatabaseError as e:
    print(f"Ошибка базы данных: {e.message}")
    # Обработать ошибку базы данных
```

---

## Пример конфигурации

### Пример полной конфигурации

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

## Дополнительная информация

- [Документация по архитектуре](ARCHITECTURE.md)
- [Руководство разработчика](DEVELOPMENT.md)
- [Репозиторий GitHub](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)

---

**Версия документа**: v2.2.10
**Дата последнего обновления**: 2026-04-28

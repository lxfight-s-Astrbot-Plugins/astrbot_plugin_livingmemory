<div align="center">

[中文](DEVELOPMENT.md) | [English](DEVELOPMENT_en.md) | [Русский](DEVELOPMENT_ru.md)

</div>

# LivingMemory — Руководство разработчика

**Версия**: v2.2.11
**Дата обновления**: 2026-05-06

---

## Содержание

1. [Настройка среды разработки](#настройка-среды-разработки)
2. [Структура проекта](#структура-проекта)
3. [Рабочий процесс разработки](#рабочий-процесс-разработки)
4. [Руководство по тестированию](#руководство-по-тестированию)
5. [Стандарты кода](#стандарты-кода)
6. [Советы по отладке](#советы-по-отладке)
7. [Руководство по внесению вклада](#руководство-по-внесению-вклада)

---

## Настройка среды разработки

### Необходимые условия

- Python 3.10+
- Среда разработки AstrBot
- Git

### Установка зависимостей

```bash
# Клонировать репозиторий
git clone https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory.git
cd astrbot_plugin_livingmemory

# Установить зависимости
pip install -r requirements.txt

# Установить зависимости для разработки
pip install pytest pytest-asyncio pytest-cov
```

### Настройка IDE

Рекомендуется: VSCode или PyCharm.

**Настройка VSCode** (`.vscode/settings.json`):
```json
{
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": true,
  "python.formatting.provider": "black",
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false
}
```

---

## Структура проекта

```
astrbot_plugin_livingmemory/
├── main.py                          # Основной файл плагина
├── core/                            # Основные модули
│   ├── exceptions.py                # Определения исключений
│   ├── config_manager.py            # Управление конфигурацией
│   ├── plugin_initializer.py       # Инициализация плагина
│   ├── event_handler.py            # Обработка событий
│   ├── command_handler.py          # Обработка команд
│   ├── tools/                      # Инструменты Agent/LLM
│   ├── memory_engine.py            # Движок памяти
│   ├── memory_processor.py         # Обработка памяти
│   ├── conversation_manager.py     # Управление разговорами
│   └── ...
├── storage/                        # Слой хранения
│   ├── conversation_store.py       # Хранилище разговоров
│   ├── db_migration.py             # Миграция базы данных
│   └── backup_manager.py           # Менеджер регулярного автобэкапа
├── pages/dashboard/                # Официальная страница плагина AstrBot
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── graph-ui.js                 # 3D-рендерер графа знаний
│   └── i18n.js                     # Движок интернационализации
├── tests/                          # Набор тестов
│   ├── conftest.py                 # Конфигурация pytest
│   ├── test_*.py                   # Модульные тесты
│   ├── integration/                # Интеграционные тесты
│   └── performance_test.py         # Тесты производительности
├── docs/                           # Документация
│   ├── API.md                      # API-документация
│   └── DEVELOPMENT.md              # Руководство разработчика
```

### Ответственность модулей

| Модуль | Ответственность | Зависимости |
|------|------|------|
| main.py | Регистрация плагина и жизненный цикл | Все основные модули |
| exceptions.py | Определения исключений | Нет |
| config_manager.py | Управление конфигурацией | config_validator |
| plugin_initializer.py | Инициализация плагина | Все основные модули |
| event_handler.py | Обработка событий | memory_engine, conversation_manager |
| command_handler.py | Обработка команд | memory_engine, conversation_manager |
| tools/ | Инкапсуляция инструментов Agent (активное восстановление и активная запись) | memory_engine, memory_processor, config_manager |

---

## Рабочий процесс разработки

### 1. Создание новой функции

```bash
# Создать новую ветку
git checkout -b feature/your-feature-name

# Разработать функцию
# ...

# Запустить тесты
pytest tests/

# Зафиксировать код
git add .
git commit -m "feat: add your feature"
git push origin feature/your-feature-name
```

### 2. Исправление ошибки

```bash
# Создать ветку исправления
git checkout -b fix/bug-description

# Исправить ошибку
# ...

# Добавить тесты
# ...

# Запустить тесты
pytest tests/

# Зафиксировать код
git add .
git commit -m "fix: fix bug description"
git push origin fix/bug-description
```

### 3. Рефакторинг кода

```bash
# Создать ветку рефакторинга
git checkout -b refactor/what-to-refactor

# Провести рефакторинг
# ...

# Убедиться, что все тесты пройдены
pytest tests/

# Зафиксировать код
git add .
git commit -m "refactor: refactor description"
git push origin refactor/what-to-refactor
```

---

## Руководство по тестированию

### Запуск тестов

```bash
# Запуск всех тестов
pytest tests/

# Запуск конкретного тестового файла
pytest tests/test_config_manager.py

# Запуск конкретной тестовой функции
pytest tests/test_config_manager.py::test_config_manager_initialization

# Просмотр покрытия
pytest --cov=core tests/

# Генерация HTML-отчёта о покрытии
pytest --cov=core --cov-report=html tests/
```

### Написание тестов

#### Пример модульного теста

```python
import pytest
from core.config_manager import ConfigManager

def test_config_manager_get():
    """Тест получения конфигурации"""
    config = ConfigManager({"key": "value"})
    assert config.get("key") == "value"
    assert config.get("non_existent", "default") == "default"

@pytest.mark.asyncio
async def test_async_function():
    """Тест асинхронной функции"""
    result = await some_async_function()
    assert result is not None
```

#### Пример использования mock

```python
from unittest.mock import Mock, AsyncMock, patch

def test_with_mock():
    """Тест с использованием mock"""
    mock_obj = Mock()
    mock_obj.method = Mock(return_value="test")

    result = mock_obj.method()
    assert result == "test"
    assert mock_obj.method.called

@pytest.mark.asyncio
async def test_with_async_mock():
    """Тест с использованием асинхронного mock"""
    mock_obj = Mock()
    mock_obj.async_method = AsyncMock(return_value="test")

    result = await mock_obj.async_method()
    assert result == "test"
```

### Тестирование новых функций

#### Тест инъекции через поддельный вызов инструмента

```python
# tests/test_event_handler.py
from unittest.mock import Mock
from core.utils import format_memories_for_fake_tool_call

def test_format_memories_for_fake_tool_call():
    """Тест форматирования поддельного вызова инструмента"""
    memories = [
        {"id": 1, "content": "Пользователь любит кошек", "score": 0.9, "importance": 0.8}
    ]
    messages = format_memories_for_fake_tool_call(memories, max_token_budget=500)
    
    assert len(messages) == 2  # tool_calls + tool
    assert messages[0]["role"] == "assistant"
    assert "tool_calls" in messages[0]
    assert messages[0]["tool_calls"][0]["id"].startswith("fake_recall_")
    assert messages[1]["role"] == "tool"
    assert "Пользователь любит кошек" in messages[1]["content"]
```

#### Тест регулярного автоматического бэкапа

```python
# tests/test_backup.py
import pytest
from storage.backup_manager import BackupManager
from core.base.config_manager import ConfigManager

@pytest.mark.asyncio
async def test_backup_cycle():
    """Тест цикла резервного копирования"""
    config = ConfigManager({"backup": {"enabled": True, "interval_hours": 24, "retention_days": 7}})
    bm = BackupManager(config)
    
    result = await bm.run_backup_cycle()
    assert "success" in result
    assert result["success"] is True or result["error"] is not None
```

#### Тест памяти описаний изображений

```python
# tests/test_event_handler.py
async def test_image_caption_memory():
    """Тест сохранения описания изображения в память"""
    event = Mock()
    event.get_messages.return_value = [Mock(role="user", text="<image_caption>кошка</image_caption>")]
    
    # Запуск обработки сообщения
    await event_handler.handle_all_group_messages(event)
    
    # Проверка, что движок памяти сохранил описание
    memories = await memory_engine.search_memories("кошка")
    assert any("кошка" in m["content"] for m in memories)
```

#### Тест фронтенда 3D-графа

```bash
# Ручное тестирование 3D-графа
# 1. Запустить WebUI
# 2. Открыть консоль браузера
# 3. Проверить, успешно ли загрузился ForceGraph3D
# 4. Проверить перетаскивание узлов, масштабирование и вращение

# Запуск модульных тестов фронтенда (если есть)
pytest tests/test_graph_ui.py
```

### Тестирование производительности

```bash
# Запуск тестов производительности
python3 tests/performance_test.py
```

---

## Стандарты кода

### Стиль кода Python

Следуйте PEP 8:

```python
# Хороший пример
def calculate_score(
    importance: float,
    recency: float,
    weight: float = 1.0
) -> float:
    """
    Рассчитать итоговый балл

    Args:
        importance: Важность
        recency: Актуальность
        weight: Вес

    Returns:
        Итоговый балл
    """
    return importance * recency * weight


# Плохой пример
def calc(i,r,w=1.0):  # Непонятные имена, нет аннотаций типов
    return i*r*w  # Нет докстринга
```

### Соглашения об именовании

- **Имена классов**: PascalCase (например, `ConfigManager`)
- **Имена функций**: snake_case (например, `get_config`)
- **Константы**: UPPER_SNAKE_CASE (например, `MAX_RETRIES`)
- **Приватные методы**: префикс подчёркивания (например, `_internal_method`)

### Докстринги

Используйте докстринги в стиле Google:

```python
def process_memory(
    content: str,
    metadata: dict,
    importance: float = 0.5
) -> tuple[str, dict, float]:
    """
    Обработать содержимое памяти

    Args:
        content: Содержимое памяти
        metadata: Словарь метаданных
        importance: Оценка важности

    Returns:
        tuple: (Обработанное содержимое, обновлённые метаданные, итоговая важность)

    Raises:
        MemoryProcessingError: Выбрасывается при сбое обработки

    Example:
        >>> content, meta, score = process_memory("test", {}, 0.8)
        >>> print(score)
        0.8
    """
    # Реализация...
    pass
```

### Аннотации типов

Все публичные функции должны иметь полные аннотации типов:

```python
from typing import Any, Optional, List, Dict

def get_memories(
    session_id: str,
    limit: int = 10,
    filters: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Получить список воспоминаний"""
    pass
```

---

## Советы по отладке

### Использование логов

```python
from astrbot.api import logger

# Разные уровни логирования
logger.debug("Отладочная информация")
logger.info("Общая информация")
logger.warning("Предупреждение")
logger.error("Информация об ошибке", exc_info=True)  # Включить трассировку стека
```

### Использование точек останова

```python
# Добавить точку останова в код
import pdb; pdb.set_trace()

# Или использовать breakpoint() (Python 3.7+)
breakpoint()
```

### Просмотр переменных

```python
# Вывести переменную
print(f"Значение переменной: {variable}")

# Использовать logger
logger.debug(f"Значение переменной: {variable}")

# Использовать pprint для форматированного вывода
from pprint import pprint
pprint(complex_dict)
```

### Профилирование производительности

```python
import time

start_time = time.time()
# Выполнить операцию
end_time = time.time()
logger.info(f"Время операции: {end_time - start_time:.4f} сек")
```

---

## Руководство по внесению вклада

### Соглашение о коммитах

Используйте Conventional Commits:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Типы**:
- `feat`: Новая функция
- `fix`: Исправление ошибки
- `docs`: Обновление документации
- `style`: Форматирование кода (не влияет на функциональность)
- `refactor`: Рефакторинг
- `test`: Связанное с тестами
- `chore`: Связанное со сборкой/инструментами

**Примеры**:
```
feat(memory): добавить затухание важности памяти

Реализован механизм затухания важности памяти на основе времени с использованием экспоненциальной функции затухания.

Closes #123
```

### Процесс Pull Request

1. Сделать форк репозитория
2. Создать функциональную ветку
3. Написать код и тесты
4. Убедиться, что все тесты пройдены
5. Отправить Pull Request
6. Дождаться ревью кода
7. Внести правки на основе обратной связи
8. Слить в основную ветку

### Чек-лист ревью кода

- [ ] Код соответствует стандартам
- [ ] Есть полные аннотации типов
- [ ] Есть докстринги
- [ ] Есть модульные тесты
- [ ] Все тесты пройдены
- [ ] Не добавлены новые зависимости (или объяснены)
- [ ] Обновлена соответствующая документация
- [ ] Сообщение коммита понятно

---

## Часто задаваемые вопросы

### В: Как добавить новый элемент конфигурации?

О:
1. Добавить определение в `_conf_schema.json`
2. Добавить логику валидации в `config_validator.py`
3. При необходимости добавить метод доступа в `ConfigManager`
4. Обновить документацию

### В: Как добавить новую команду?

О:
1. Добавить метод обработки в `CommandHandler`
2. Добавить декоратор команды в `main.py`
3. Добавить модульные тесты
4. Обновить справочную документацию

### В: Как добавить новый язык в WebUI?

О:
1. Добавить новый языковой словарь в объект `TRANSLATIONS` в `pages/dashboard/i18n.js`
2. Убедиться, что все ключи `data-i18n` имеют соответствующие переводы
3. Добавить новый пункт в `<select>` в HTML
4. Протестировать логику автоматического определения (`navigator.language`) и ручного переключения

### В: Как отлаживать проблемы инициализации?

О:
1. Проверить вывод логов
2. Использовать `initialization_status_callback` для получения статуса
3. Проверить готовность Provider
4. Проверить свойство `_initialization_error`

---

## Полезные ссылки

- [API-документация](API.md)
- [Документация по архитектуре](ARCHITECTURE.md)
- [Репозиторий GitHub](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)
- [Обратная связь по ошибкам](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues)

---

**Версия документа**: v2.2.11
**Дата последнего обновления**: 2026-05-06

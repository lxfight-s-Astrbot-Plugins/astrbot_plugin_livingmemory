<div align="center">

[中文](ARCHITECTURE.md) | [English](ARCHITECTURE_en.md) | [Русский](ARCHITECTURE_ru.md)

</div>

# LivingMemory Architecture Documentation

**Version**: v2.2.10
**Last Updated**: 2026-04-28

---

## Directory Structure

```
astrbot_plugin_livingmemory/
├── main.py                          # Plugin main file
├── core/                            # Core modules
│   ├── __init__.py                  # Core module exports
│   ├── plugin_initializer.py       # Plugin initializer
│   ├── event_handler.py            # Event handler
│   ├── command_handler.py          # Command handler
│   ├── tools/                      # Agent/LLM tools
│   │
│   ├── base/                        # Base component layer
│   │   ├── __init__.py
│   │   ├── exceptions.py            # Exception definitions
│   │   ├── constants.py             # Constant definitions
│   │   ├── config_manager.py        # Configuration manager
│   │   └── config_validator.py      # Configuration validator
│   │
│   ├── models/                      # Data model layer
│   │   ├── __init__.py
│   │   └── conversation_models.py   # Conversation data models
│   │
│   ├── managers/                    # Manager layer
│   │   ├── __init__.py
│   │   ├── conversation_manager.py  # Conversation manager
│   │   └── memory_engine.py         # Memory engine
│   │
│   ├── processors/                  # Processor layer
│   │   ├── __init__.py
│   │   ├── memory_processor.py      # Memory processor
│   │   ├── text_processor.py        # Text processor
│   │   ├── chatroom_parser.py       # Chatroom parser
│   │   └── message_utils.py         # Message utilities
│   │
│   ├── validators/                  # Validator layer
│   │   ├── __init__.py
│   │   └── index_validator.py       # Index validator
│   │
│   ├── retrieval/                   # Retrieval system layer
│   │   ├── __init__.py
│   │   ├── dual_route_retriever.py  # Document-route/graph-route dual fusion
│   │   ├── hybrid_retriever.py      # Document-route hybrid retriever
│   │   ├── bm25_retriever.py        # BM25 retriever
│   │   ├── vector_retriever.py      # Vector retriever
│   │   ├── graph_keyword_retriever.py # Graph-route keyword retrieval
│   │   ├── graph_vector_retriever.py  # Graph-route vector retrieval
│   │   ├── graph_retriever.py       # Graph-route fusion retriever
│   │   └── rrf_fusion.py            # RRF fusion
│   │
│   ├── utils/                       # Utilities layer
│   │   ├── __init__.py
│   │   ├── stopwords_manager.py     # Stopwords manager
│   │   └── (FakeToolCallFormatter)   # Fake tool call formatter
│   │
│   ├── tools/                       # Agent tool layer
│   │   ├── __init__.py
│   │   └── memory_search_tool.py    # Proactive long-term memory recall tool
│   │
│   └── prompts/                     # Prompt templates
│       ├── private_chat_prompt.txt
│       └── group_chat_prompt.txt
│
├── storage/                         # Storage layer
│   ├── __init__.py
│   ├── conversation_store.py       # Conversation storage
│   ├── db_migration.py             # Database migration
│   └── backup_manager.py           # Scheduled auto-backup manager
│
├── webui/                           # Web management interface
│   ├── __init__.py
│   ├── server.py                    # FastAPI server
│   └── README.md
│
├── static/                          # Static resources
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── graph-ui.js                  # 3D knowledge graph renderer
│   └── i18n.js                      # Internationalization engine
│
├── tests/                           # Test suite
│   ├── conftest.py
│   ├── test_*.py
│   └── integration/
│
└── docs/                            # Documentation
    ├── API.md
    ├── DEVELOPMENT.md
    └── ARCHITECTURE.md (this document)
```

---

## Layered Architecture

### 1. Base Component Layer (base/)

**Responsibility**: Provide the most fundamental components, depended upon by all other layers.

**Components**:
- `exceptions.py`: Custom exception classes
- `constants.py`: Constant definitions
- `config_manager.py`: Configuration management
- `config_validator.py`: Configuration validation

**Dependencies**: None (lowest layer)

**Depended upon**: All other layers

---

### 2. Data Model Layer (models/)

**Responsibility**: Define data structures and models.

**Components**:
- `conversation_models.py`: Message, Session, MemoryEvent, and other data models.

**Dependencies**: base/

**Depended upon**: managers/, processors/, storage/

---

### 3. Processor Layer (processors/)

**Responsibility**: Process and transform data.

**Components**:
- `memory_processor.py`: Use LLM to process conversation history.
- `text_processor.py`: Text tokenization and processing.
- `chatroom_parser.py`: Chatroom context parsing.
- `message_utils.py`: Message processing utilities.

**Dependencies**: base/, models/

**Depended upon**: managers/, event_handler.py

---

### 4. Retrieval System Layer (retrieval/)

**Responsibility**: Implement memory retrieval functionality.

**Components**:
- `dual_route_retriever.py`: Coordinate final ranking of document-route and graph-route.
- `hybrid_retriever.py`: Document-route hybrid retriever (BM25 + vector).
- `bm25_retriever.py`: Document-route keyword retrieval.
- `vector_retriever.py`: Document-route vector retrieval.
- `graph_keyword_retriever.py`: Graph-route keyword retrieval.
- `graph_vector_retriever.py`: Graph-route vector retrieval.
- `graph_retriever.py`: Graph-route result fusion.
- `rrf_fusion.py`: RRF fusion algorithm.

**Dependencies**: base/, processors/text_processor

**Depended upon**: managers/memory_engine

---

### 5. Manager Layer (managers/)

**Responsibility**: Manage core business logic.

**Components**:
- `memory_engine.py`: Memory engine (core).
- `conversation_manager.py`: Conversation management.

**Dependencies**: base/, models/, processors/, retrieval/

**Depended upon**: plugin_initializer, event_handler, command_handler

---

### 6. Validator Layer (validators/)

**Responsibility**: Validate data and indexes.

**Components**:
- `index_validator.py`: Index consistency validation.

**Dependencies**: base/, managers/

**Depended upon**: plugin_initializer, command_handler

---

### 7. Utilities Layer (utils/)

**Responsibility**: Provide general utility functions and formatters.

**Components**:
- `stopwords_manager.py`: Stopwords management.
- `FakeToolCallFormatter` (`__init__.py`): Fake tool call formatter.
  - Wraps memory content as `tool_calls` + `tool` message pairs.
  - Uses a fixed prefix `fake_recall_` as the call_id, making it easy for `EventHandler` to clean up automatically.
  - Supports token budget truncation to avoid exceeding context limits.

**Dependencies**: base/

**Depended upon**: processors/, retrieval/, event_handler.py

---

### 8. Agent Tool Layer (tools/)

**Responsibility**: Provide business tools that can be actively called for AstrBot's tool loop / agent mode.

**Components**:
- `memory_search_tool.py`: Proactive long-term memory recall tool, reusing the existing memory retrieval engine and filtering configuration.

**Dependencies**: base/, managers/, utils/

**Depended upon**: main.py (registered to AstrBot context)

---

### 9. Storage Layer (storage/)

**Responsibility**: Data persistence and backup.

**Components**:
- `conversation_store.py`: Conversation data storage.
- `db_migration.py`: Database migration.
- `backup_manager.py`: Scheduled auto-backup manager.
  - Interval-based backup scheduling (cron / asyncio sleep).
  - Configurable retention policy (by days or count).
  - Automatic retry and alerting on failure.

**Dependencies**: base/, models/

**Depended upon**: managers/conversation_manager, plugin_initializer

---

### 10. Top-Level Components

**Components**:
- `plugin_initializer.py`: Plugin initialization.
- `event_handler.py`: Event handling.
- `command_handler.py`: Command handling.
- `main.py`: Plugin registration.
- `tools/`: Agent tool registration entry implementation source.

**Dependencies**: All lower-layer components.

**Depended upon**: None (highest layer)

---

## Dependency Graph

```
main.py
  ↓
plugin_initializer.py, event_handler.py, command_handler.py, tools/
  ↓
managers/ (memory_engine, conversation_manager)
  ↓
processors/, retrieval/, validators/, storage/
  ↓
models/
  ↓
base/ (exceptions, config, constants)
```

---

## Import Conventions

### Importing from core

```python
# Recommended: import from core top-level
from core import (
    ConfigManager,
    MemoryEngine,
    ConversationManager,
    MemoryProcessor,
    Message,
    Session,
)

# Also possible: import from submodules
from core.base import ConfigManager
from core.managers import MemoryEngine
from core.models import Message
```

### Importing inside core

```python
# In core/managers/memory_engine.py
from ..base import ConfigManager, LivingMemoryException
from ..models import Message
from ..processors import TextProcessor
from ..retrieval import HybridRetriever
```

### Importing inside submodules

```python
# In core/managers/conversation_manager.py
from ..base import ConfigManager
from ..models import Message, Session
from ...storage import ConversationStore
```

---

## Design Principles

### 1. Single Responsibility Principle (SRP)

Each module is responsible for only one clear functional domain:
- base/: Infrastructure
- models/: Data structures
- processors/: Data processing
- managers/: Business logic
- validators/: Validation logic

### 2. Dependency Inversion Principle (DIP)

High-level modules do not depend on low-level modules; both depend on abstractions:
- Use interfaces and abstract classes.
- Pass dependencies via dependency injection.

### 3. Open-Closed Principle (OCP)

Open for extension, closed for modification:
- New features are implemented by adding new modules.
- Existing stable code is not modified.

### 4. Interface Segregation Principle (ISP)

Clients should not be forced to depend on interfaces they do not use:
- Each module exposes only necessary interfaces.
- Use `__all__` to explicitly define exports.

### 5. Law of Demeter (LoD)

Modules should only interact with their immediate dependencies:
- Avoid cross-layer calls.
- Pass requests through intermediate layers.

---

## Module Responsibility Matrix

| Layer | Responsibility | Example |
|------|------|------|
| base/ | Infrastructure | Exceptions, config, constants |
| models/ | Data definitions | Message, Session |
| processors/ | Data processing | Text processing, LLM calls |
| retrieval/ | Retrieval implementation | Document-route, graph-route, RRF fusion |
| managers/ | Business logic | Memory management, conversation management |
| validators/ | Validation logic | Index validation |
| utils/ | Utility functions | Stopwords, auxiliary functions |
| storage/ | Data persistence | SQLite operations |

---

### WebUI Frontend Architecture

**Files**:
- `index.html`: Single-page app shell with language switcher and dark-mode toggle.
- `app.js`: Main dashboard logic (memory table, search, pagination, CRUD).
- `graph-ui.js`: 3D knowledge graph renderer.
  - Force-directed graph based on `ForceGraph3D` (Three.js).
  - Supports node drag, zoom, rotate, hover tooltips.
  - Entity-type coloring: person, place, event, concept, other.
  - Communicates with `app.js` via an event bus, sharing filter criteria.
- `i18n.js`: Internationalization engine.
  - Supports `zh` / `en` / `ru` (Russian as fallback).
  - DOM translation based on `data-i18n` attributes.
  - `localStorage` persistence + `navigator.language` auto-detection.
- `styles.css`: Glassmorphism design system.

---

## Extension Guide

### Adding a new processor

1. Create a new file in `core/processors/`.
2. Implement the processor class.
3. Export it in `core/processors/__init__.py`.
4. Import and use where needed.

### Adding a new retriever

1. Create a new file in `core/retrieval/`.
2. Implement the retriever interface.
3. Integrate it into `HybridRetriever`.
4. Update configuration and documentation.

### Adding a new manager

1. Create a new file in `core/managers/`.
2. Implement the manager class.
3. Export it in `core/managers/__init__.py`.
4. Integrate it into the initializer.

---

## Best Practices

### 1. Import Order

```python
# Standard library
import os
import time

# Third-party libraries
import aiosqlite
from astrbot.api import logger

# Local imports - in layer order
from ..base import ConfigManager, LivingMemoryException
from ..models import Message
from ..processors import TextProcessor
```

### 2. Avoiding Circular Dependencies

- Use string-form type annotations.
- Use lazy imports (import inside functions).
- Redesign module boundaries.

### 3. Interface Design

- Use type annotations.
- Provide docstrings.
- Clarify parameters and return values.

### 4. Error Handling

- Use custom exceptions.
- Catch at the appropriate layer.
- Provide clear error messages.

---

## Version History

### v2.2.10

- **Fake Tool Call Injection**: Added fake tool call injection strategy, compatible with Agent / Tool Loop mode.
- **Image Caption Memory**: Supports automatic storage of AstrBot image caption results into long-term memory.
- **3D Knowledge Graph WebUI**: Added 3D force-directed graph visualization based on ForceGraph3D.
- **Scheduled Auto-Backup**: Added scheduled auto-backup subsystem (`backup_manager.py`).
- **Safe Batched Index Rebuild**: Index rebuild supports safe batched mode to avoid memory overflow.
- **i18n**: WebUI added full internationalization support (Chinese/English/Russian); command responses fully translated to English.

### v2.0.0 (2025-12-17)

- Reorganized directory structure.
- Introduced layered architecture.
- Improved module responsibility division.
- Completed directory reorganization validation.

**Validation Results**:
- ✓ Directory structure validation passed.
- ✓ `__init__.py` export validation passed.
- ✓ Import path validation passed.
- ✓ 7 functional subdirectories, 18 Python files.
- ✓ 13 files' import paths updated.

---

**Document Version**: v2.2.10
**Last Updated**: 2026-04-28

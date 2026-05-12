<div align="center">

[中文](README_zh.md) | [English](README.md) | [Русский](README_ru.md)

</div>

# LivingMemory - Intelligent Long-Term Memory Plugin with Dynamic Lifecycle

**Version**: v2.2.11 | **Author**: lxfight | **License**: AGPLv3

---

## Core Features

- **Hybrid Retrieval**: Combines BM25 sparse retrieval and Faiss vector retrieval with RRF fusion.
- **Dual-Route Four-Mode Retrieval**: Maintains both document and graph routes, each supporting keyword and vector retrieval before unified ranking.
- **Intelligent Summarization**: Uses an LLM to summarize conversation history into structured memories.
- **Dual-Channel Summarization**: Stores `canonical_summary` and `persona_summary` separately for retrieval and prompt injection.
- **Session Isolation**: Supports persona-level and session-level memory isolation.
- **Agent Memory Tools**: Exposes `recall_long_term_memory` and `memorize_long_term_memory` so agents can actively recall or write long-term memories when needed.
- **Auto-Forgetting**: Cleans up stale memories based on time and importance.
- **Data Safety**: Includes automatic backup before migration, rollback on index rebuild failure, and transactional deletion.
- **WebUI Management**: Supports both the AstrBot official plugin Pages dashboard and the legacy standalone WebUI compatibility entry.

---

## Quick Start

### Installation

Place the plugin folder under AstrBot's `data/plugins` directory. AstrBot installs dependencies automatically.

### Configuration

Configure the plugin from the AstrBot plugin configuration page.

**Required settings**:
- `embedding_provider_id`: Embedding model ID. Leave empty to use the AstrBot default.
- `llm_provider_id`: LLM model ID. Leave empty to use the AstrBot default.

**Memory injection compatibility**:
- `fake_tool_call` automatically falls back to `user_message_before` for Gemini providers to avoid tool-message protocol incompatibility.
- For DeepSeek V4 `thinking` mode, select `fake_tool_call_deepseek_v4` manually if you need fake-tool-call semantics.

**WebUI settings**:
```json
{
  "webui_settings": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8080,
    "access_password": "your_password"
  }
}
```

### AstrBot Version Requirement

- The **AstrBot official plugin Pages dashboard** requires **AstrBot >= 4.24.2**.
- The legacy standalone WebUI compatibility entry does not depend on plugin Pages and can still be opened through `/lmem webui`.

### Management Entry

Recommended entry:

1. Open the AstrBot official WebUI.
2. Go to `Plugins -> LivingMemory -> Pages -> dashboard`.

Compatibility entry:

- Run `/lmem webui`.
- If the legacy standalone WebUI is enabled, open the returned URL.

---

## Commands

| Command | Description |
| :--- | :--- |
| `/lmem status` | View memory status |
| `/lmem search <query> [k]` | Search memories (default 5 items) |
| `/lmem forget <id>` | Delete a specific memory |
| `/lmem rebuild-index` | Rebuild indexes |
| `/lmem rebuild-graph` | Rebuild graph memory indexes |
| `/lmem webui` | View WebUI information |
| `/lmem summarize` | Trigger immediate summarization for the current session |
| `/lmem reset` | Reset current session memory context |
| `/lmem cleanup [preview\|exec]` | Clean injected memory fragments from history |
| `/lmem help` | Show help |

---

## Architecture

### Module Structure

```
astrbot_plugin_livingmemory/
├── main.py                          # Plugin registration and lifecycle management
├── core/
│   ├── base/                        # Base components
│   ├── managers/                    # Core managers
│   ├── retrieval/                   # Retrieval layer
│   ├── validators/                  # Validators
│   ├── plugin_initializer.py        # Plugin initializer
│   ├── event_handler.py             # Event handler
│   └── command_handler.py           # Command handler
├── storage/                         # Storage layer
├── pages/                           # AstrBot official plugin Pages assets
├── webui/                           # Legacy standalone WebUI compatibility entry
├── tests/                           # Test suite
└── docs/                            # Documentation
```

### Core Components

1. **PluginInitializer**
   - Non-blocking initialization
   - Provider wait and retry
   - Automatic database migration

2. **EventHandler**
   - Group message capture
   - Memory recall
   - Memory reflection

3. **Agent Memory Tools**
   - `recall_long_term_memory`: actively recalls long-term memories, reusing current session/persona filtering settings and returning raw memory results
   - `memorize_long_term_memory`: actively writes long-term memories, always using the current UMO and persona while reusing the automatic summarization storage format
   - Useful for scenarios such as “do you remember”, “what did I say before”, and “please remember ...”

4. **CommandHandler**
   - Unified command responses
   - Structured error handling

5. **PluginPageApi**
   - Registers plugin page APIs through `register_web_api`
   - Reuses the runtime memory engine and graph components
   - Provides memory management, recall debugging, and graph queries for `pages/dashboard`

6. **ConfigManager**
   - Centralized configuration loading
   - Configuration validation
   - Nested key access

---

## Agent Memory Tools

The plugin registers two LLM tools at runtime so agents can actively manage long-term memory:

- `recall_long_term_memory`: actively recalls existing memories. Use it when the user asks what the bot remembers, asks about previous context, or when ambiguous references require checking history. Prefer short keywords such as topics, entities, preferences, or agreements.
- `memorize_long_term_memory`: actively writes long-term memory. Use it when the user explicitly asks the bot to remember something, or when stable preferences, durable facts, agreements, identity details, or long-lived project context appear.

---

## Developer Guide

### Testing

```bash
# Run all tests
pytest tests/

# Run a specific test
pytest tests/test_config_manager.py

# Show coverage
pytest --cov=core tests/
```

### Documentation

- [API Documentation](docs/API_en.md): Detailed API reference
- [Architecture Documentation](docs/ARCHITECTURE_en.md): Architecture overview
- [Developer Guide](docs/DEVELOPMENT_en.md): Development and contribution guide

---

## Data Migration (v1.4.0-v1.4.2)

If you upgrade from v1.4.0-v1.4.2, old data may not migrate automatically. Manual recovery steps:

1. Locate the backup file: `data/plugin_data/astrbot_plugin_livingmemory/backups/livingmemory_backup_<timestamp>.db`
2. Move it to `data/plugin_data/astrbot_plugin_livingmemory/`
3. Rename it to `livingmemory.db`
4. Reload the plugin. The system will load and process the data automatically.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## Support

- **GitHub**: [astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)
- **Issues**: [GitHub Issues](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues)
- **QQ Group**: [![Join QQ Group](https://img.shields.io/badge/QQ%20Group-953245617-blue?style=flat-square&logo=tencent-qq)](https://qm.qq.com/cgi-bin/qm/qr?k=WdyqoP-AOEXqGAN08lOFfVSguF2EmBeO&jump_from=webapi&authKey=tPyfv90TVYSGVhbAhsAZCcSBotJuTTLf03wnn7/lQZPUkWfoQ/J8e9nkAipkOzwh)
  (Password: lxfight)

---

## License

This project is licensed under AGPLv3.

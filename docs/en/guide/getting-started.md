# Quick Start

LivingMemory is a long-term memory plugin for AstrBot. It maintains a searchable memory store outside the immediate chat window so the bot can remember stable preferences, long-running projects, relationships, group context, and past agreements.

## Install

The current stable version is **2.9.1** (previous version: 2.9.0). See [Releases and Upgrades](/en/releases) for tagged downloads, upgrade and rollback steps.

1. Put the plugin directory under AstrBot's `data/plugins/` directory.
2. Restart or reload AstrBot.
3. AstrBot will install Python dependencies from `requirements.txt`.
4. Open the AstrBot plugin configuration page and select `LivingMemory`.

If every dashboard API reports a missing route ([#374](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/374)), confirm that `networkx` is installed in the environment running AstrBot. Installing it resolved the reported failure; `networkx>=3.0` is already declared in the requirements. Run the following from the AstrBot root using the same Python environment to install all dependencies, then restart:

```bash
python -m pip install -r data/plugins/astrbot_plugin_livingmemory/requirements.txt
```

The plugin declares its direct Python dependencies in `requirements.txt`. It uses the `faiss-cpu` version provided by AstrBot to avoid replacing the host runtime.

## Timestamp write errors

If memory writes report `Datetime values must have timezone information` ([#370](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/370)), upgrade **AstrBot to 4.29.0 or later**, then restart it. Retain backups of AstrBot configuration and plugin data before upgrading.

Since 0.0.45, SQLModel infers UTC-aware types for ordinary `datetime` fields. Older AstrBot versions such as 4.24.2 still insert naive timestamps into their document models, causing writes to fail. AstrBot [#10211](https://github.com/AstrBotDevs/AstrBot/pull/10211) explicitly preserves the existing local-time type and was released in 4.29.0. Updating LivingMemory alone cannot replace the host model. The host fix preserves historical timestamp semantics and does not require regenerating embeddings.

## Required configuration

> Advanced options (recall, reflection, graph, atoms, consolidation, backups and more) now live on the dashboard Settings page; the AstrBot plugin config page keeps only providers and language.


| Key | Purpose | Recommendation |
| --- | --- | --- |
| `provider_settings.embedding_provider_id` | Generates vectors for semantic retrieval | Required: fill in an enabled Embedding Provider ID from AstrBot (for example `openai_embedding`) |
| `provider_settings.llm_provider_id` | Summarizes conversations and evaluates memory | Leave empty to use the default LLM; a stable reasoning model is recommended |
| `bot_language` | Language for command and status replies | `zh`, `en`, `ru`, or `ja` |

## Embedding model

LivingMemory's semantic recall depends on an Embedding Provider, not the chat LLM. The free `BAAI/bge-m3` model (1024 dimensions) is recommended; see [Embedding Models and BGE-M3](/en/guide/embedding-model) for the full workflow from obtaining a key and creating a provider to finding its ID.

## Recommended settings

| Scenario | Recommendation |
| --- | --- |
| Private assistant | Enable persona and session filtering to avoid cross-persona memories |
| Long-running group chat | Enable `enable_full_group_capture` to capture context that does not directly mention the bot |
| Agent / tool loop | Keep agent memory tools enabled so the model can recall or write memory when useful |
| Injection mode | Only `extra_user_content` is supported (add-only temporary part, never written into conversation history); legacy modes fall back automatically |

## Open the dashboard

AstrBot `4.24.2` or later is recommended. Open:

`Plugins -> LivingMemory -> Views -> dashboard` (`Pages` in older AstrBot releases)

The dashboard lets you inspect memories, debug recall, manage backups, and browse graph relationships.

## Verify the setup

After several turns of conversation, try:

```text
/lmem status
/lmem summarize
/lmem search your keywords
```

If status and search results appear, the basic pipeline is working.

# Quick Start

LivingMemory is a long-term memory plugin for AstrBot. It maintains a searchable memory store outside the immediate chat window so the bot can remember stable preferences, long-running projects, relationships, group context, and past agreements.

## Install

1. Put the plugin directory under AstrBot's `data/plugins/` directory.
2. Restart or reload AstrBot.
3. AstrBot will install Python dependencies from `requirements.txt`.
4. Open the AstrBot plugin configuration page and select `LivingMemory`.

## Required configuration

| Key | Purpose | Recommendation |
| --- | --- | --- |
| `provider_settings.embedding_provider_id` | Generates vectors for semantic retrieval | Leave empty to use AstrBot's default embedding provider |
| `provider_settings.llm_provider_id` | Summarizes conversations and evaluates memory | Leave empty to use the default LLM; a stable reasoning model is recommended |
| `bot_language` | Language for command and status replies | `zh`, `en`, or `ru` |

## Recommended settings

| Scenario | Recommendation |
| --- | --- |
| Private assistant | Enable persona and session filtering to avoid cross-persona memories |
| Long-running group chat | Enable `enable_full_group_capture` to capture context that does not directly mention the bot |
| Agent / tool loop | Keep agent memory tools enabled so the model can recall or write memory when useful |
| Gemini provider | `fake_tool_call` automatically falls back to `extra_user_content` |
| DeepSeek V4 thinking | Use normal `fake_tool_call`; the legacy `fake_tool_call_deepseek_v4` option is only a compatibility alias |

## Open the dashboard

AstrBot `4.24.2` or later is recommended. Open:

`Plugins -> LivingMemory -> Pages -> dashboard`

The dashboard lets you inspect memories, debug recall, manage backups, and browse graph relationships.

## Verify the setup

After several turns of conversation, try:

```text
/lmem status
/lmem summarize
/lmem search your keywords
```

If status and search results appear, the basic pipeline is working.

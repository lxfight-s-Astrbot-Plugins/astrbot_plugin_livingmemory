<p align="center">
  <a href="README.md">中文</a> · <strong>English</strong> · <a href="README_ru.md">Русский</a>
</p>

![LivingMemory: long-term memory connecting preferences, people, plans and context](docs/public/images/livingmemory-cover.svg)

<p align="center">
  <a href="https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/"><strong>Documentation</strong></a> ·
  <a href="https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/webui"><strong>Dashboard</strong></a> ·
  <a href="https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases"><strong>Downloads</strong></a> ·
  <a href="https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues"><strong>Issues</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-52675c?style=flat-square" alt="Python 3.10 or later">
  <img src="https://img.shields.io/badge/AstrBot-4.24.2%2B-52675c?style=flat-square" alt="Pages requires AstrBot 4.24.2 or later">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-52675c?style=flat-square" alt="AGPL-3.0"></a>
</p>

## Keep what matters from a conversation

LivingMemory gives AstrBot a durable store of preferences, relationships, project progress and past agreements. It summarizes conversations, recalls relevant context through keywords, vectors and a graph, and manages memory through archiving, decay and access reinforcement.

> **In testing: 2.7.0-beta.1**<br>
> Four dashboard styles, steadier graph labels and clearer editing and import workflows.<br>
> [Download beta](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.1) · [Releases and upgrades](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/releases) · [Stable 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1)

## From remembering to recalling

- **Automatic capture** — Summarize conversations after the configured number of turns. Important memories can retain source messages for review and re-summarization.
- **Context-aware recall** — Document and graph routes combine keyword and vector retrieval, rank fusion, scope filtering and lifecycle checks.
- **Agent tools** — `recall_long_term_memory` and `memorize_long_term_memory` let the agent read and write long-term memory when needed.
- **Visible and maintainable** — Browse relationships, edit memories, test recall, manage prompts and inspect system status in official Plugin Pages.

## One workspace, four styles

**Editorial** offers green grids and crisp lines; **Studio** uses soft cards; **Paper** pairs warm surfaces with serif headings; **Terminal** combines monospace type with instrument panels.

Every style supports light, dark and automatic modes. Switch from “Appearance” in the dashboard; preferences stay in the current browser. Graph labels settle during zoomed reading, and mobile controls support session filtering and memory focus.

[Explore themes and workflows →](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/webui)

## Get started

1. Install from the AstrBot plugin marketplace, or place the selected version under `data/plugins/astrbot_plugin_livingmemory`.
2. Reload AstrBot and choose Embedding and LLM providers in LivingMemory settings; empty fields use AstrBot defaults.
3. Open `Plugins → LivingMemory → Pages → dashboard`. Pages requires **AstrBot 4.24.2 or later**.

For the beta, download the specific tag above and follow [installation and rollback steps](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/releases). Back up existing plugin data and configuration before upgrading.

After a few conversation turns, use `/lmem status`, `/lmem summarize` and `/lmem search your keywords` to check the pipeline.

## Keep exploring

[Quick start](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/guide/getting-started) · [Configuration](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/configuration) · [Commands](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/commands) · [Architecture](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/architecture) · [Changelog](CHANGELOG.md)

Upgrading from v1.4.0–v1.4.2? Read the [backup and migration guide](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/configuration#backup-migration-and-cleanup) first.

---

Community: [QQ group 953245617](https://qm.qq.com/cgi-bin/qm/qr?k=WdyqoP-AOEXqGAN08lOFfVSguF2EmBeO&jump_from=webapi&authKey=tPyfv90TVYSGVhbAhsAZCcSBotJuTTLf03wnn7/lQZPUkWfoQ/J8e9nkAipkOzwh) · Password: `lxfight`<br>
License: [AGPL-3.0](LICENSE)

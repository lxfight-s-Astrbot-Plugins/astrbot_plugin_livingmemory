---
layout: home
title: LivingMemory
titleTemplate: Intelligent long-term memory plugin
hero:
  name: LivingMemory
  text: Intelligent long-term memory for AstrBot
  tagline: Preserve durable preferences, relationships, agreements, and project context while keeping memory fresh through a controllable lifecycle.
  image:
    src: https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/raw/master/logo.png
    alt: LivingMemory logo
  actions:
    - theme: brand
      text: Quick Start
      link: /en/guide/getting-started
    - theme: alt
      text: Get 2.14.0
      link: /en/releases
features:
  - title: Automatic long-term memory
    details: Conversations are summarized into searchable long-term memories after the configured trigger rounds.
  - title: Agent recall and write tools
    details: Registers recall_long_term_memory and memorize_long_term_memory for agent/tool-loop scenarios.
  - title: Dual-route retrieval
    details: Document and graph routes each support keyword and vector retrieval, then fuse by normalized scores.
  - title: Time-aware lifecycle
    details: Memory atoms have TTL, decay, access reinforcement, and cleanup behavior.
  - title: Four dashboard styles
    details: macOS 26 Liquid Glass, Studio, Paper and Terminal; light/dark modes, custom accent, wallpaper and CSS, and clear editing and import workflows.
  - title: Data safety
    details: Version backups, pre-migration backups, index rollback, and transactional deletion reduce upgrade risk.
---

```mermaid
flowchart LR
    MSG["Conversation message"] --> HOOK["EventHandler hooks"]
    HOOK -->|"Before request"| RECALL["Dual-route recall"]
    HOOK -->|"After reply"| REFLECT["Reflection and summarization"]
    RECALL --> ENGINE["MemoryEngine"]
    REFLECT --> ENGINE
    ENGINE --> SQLITE[("SQLite memory store")]
    ENGINE --> FAISS[("FAISS vector index")]
    ENGINE --> API["Dashboard and agent tools"]
```

## 2.14.0 is released

2.14.0 upgrades memory quality, cross-adapter scoping, and the dashboard 3D galaxy view: it fixes atom classification, summary-parse regex fallback, graph-entry CJK bigram FTS, and passive-injection access inflation; adds a group memory blacklist, platform-independent user scoping, private-memory visibility notices, and live Embedding Provider resolution; and upgrades the 3D graph into multi-morphology galaxies with a deep-space HUD alongside an on-demand categorized Settings workspace. Public commands and database schema remain compatible. See [Releases and Upgrades](/en/releases) for downloads and upgrade steps.

## Who is this for?

Start with [Quick Start](/en/guide/getting-started) if you want to install and use the plugin.  
Read [Features](/en/features) and [Architecture](/en/architecture) if you want to understand how the memory system stores facts, retrieves relationships, and ages older context.

::: tip Documentation scope
This site keeps the user guide, feature explanation, and architecture overview. Old phase notes, internal development docs, and outdated API drafts are intentionally left out.
:::

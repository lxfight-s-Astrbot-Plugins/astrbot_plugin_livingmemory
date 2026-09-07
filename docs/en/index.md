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
      text: Try 2.7.0-beta.1
      link: /en/releases
features:
  - title: Automatic long-term memory
    details: Conversations are summarized into searchable long-term memories after the configured trigger rounds.
  - title: Agent recall and write tools
    details: Registers recall_long_term_memory and memorize_long_term_memory for agent/tool-loop scenarios.
  - title: Dual-route retrieval
    details: Document and graph routes each support keyword and vector retrieval, then merge rankings with RRF.
  - title: Time-aware lifecycle
    details: Memory atoms have TTL, decay, access reinforcement, and cleanup behavior.
  - title: Four dashboard styles
    details: Editorial, Studio, Paper and Terminal; light/dark modes, steady graph reading, and clear editing and import workflows.
  - title: Data safety
    details: Version backups, pre-migration backups, index rollback, and transactional deletion reduce upgrade risk.
---

<img class="diagram" src="/images/architecture-flow.svg" alt="LivingMemory runtime architecture">

## 2.7.0-beta.1 is in testing

This beta includes every merged change since 2.6.1: recall and index recovery fixes, four styles, stable graph labels and clearer dashboard workflows. [Releases and Upgrades](/en/releases) lists all PRs and authors, downloads, installation and rollback steps. The stable version remains 2.6.1.

## Who is this for?

Start with [Quick Start](/en/guide/getting-started) if you want to install and use the plugin.  
Read [Features](/en/features) and [Architecture](/en/architecture) if you want to understand how the memory system stores facts, retrieves relationships, and ages older context.

::: tip Documentation scope
This site keeps the user guide, feature explanation, and architecture overview. Old phase notes, internal development docs, and outdated API drafts are intentionally left out.
:::

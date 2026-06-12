# Architecture

LivingMemory is built from event hooks, memory processing, retrieval fusion, storage, and a Pages API. Automatic memory and active agent tools share the same core data model so they do not become two separate memory systems.

<img class="diagram" src="/images/architecture-flow.svg" alt="LivingMemory runtime architecture">

## Runtime flow

1. AstrBot receives a message and `EventHandler` captures session context.
2. Before the LLM request, the recall pipeline searches long-term memory using the current message and optional recent context.
3. Retrieved memories are injected into the request or returned as agent tool results.
4. After the LLM responds, the reflection pipeline decides whether to summarize and store new memory.
5. Background tasks handle decay, cleanup, backup, and index validation.

## Main modules

| Module | Responsibility |
| --- | --- |
| `main.py` | Registers the plugin, initializes runtime components, registers agent tools and Pages API |
| `core/plugin_initializer.py` | Non-blocking initialization, provider waiting, database migration, index loading |
| `core/event_handler.py` | Group capture, memory recall, memory reflection |
| `core/managers/memory_engine.py` | Unified write, search, delete, and index maintenance |
| `core/managers/graph_memory_manager.py` | Coordinates graph nodes, edges, entries, and graph retrieval |
| `core/managers/atom_lifecycle_manager.py` | Maintains atom expiration, forgetting, reinforcement, and lifecycle state |
| `core/retrieval/` | BM25, vector, graph, atom retrieval, and RRF fusion |
| `storage/` | SQLite storage, graph storage, atom storage, database migration |
| `pages/dashboard/` | AstrBot Pages management UI |

## Dual-route retrieval

Document memories and graph memories are searched through two routes:

| Route | Keyword mode | Vector mode |
| --- | --- | --- |
| Document route | `BM25Retriever` | `VectorRetriever` |
| Graph route | `GraphKeywordRetriever` | `GraphVectorRetriever` |

`RRFFusion` merges the ranked lists, then the runtime applies importance, time decay, session filtering, and persona filtering.

## Memory data model

| Type | Description |
| --- | --- |
| Session messages | Raw conversation context used for summarization triggers and expanded queries |
| Memory entries | LLM-generated long-term memories with summaries, importance, session, and persona metadata |
| Graph nodes and edges | Entities and relationships extracted from memories, with cross-memory merging |
| Memory atoms | Independent fact units with type, TTL, decay, and access reinforcement |

## Data safety

| Scenario | Protection |
| --- | --- |
| Plugin version change | Startup creates a version-tagged backup |
| Database migration | Backup before migration |
| Index rebuild | Batched rebuild with rollback on failure |
| Memory deletion | Transactional deletion of related records |
| Dashboard operations | Pages API reuses MemoryEngine and GraphStore instead of bypassing backend safety logic |

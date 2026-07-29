# WebUI

LivingMemory uses AstrBot official Plugin Pages for its dashboard. No extra web server is required.

## Entry

Open AstrBot WebUI:

`Plugins -> LivingMemory -> Pages -> dashboard`

AstrBot `4.24.2` or later is recommended. Older versions can still run the plugin, but the dashboard may be unavailable.

## Dashboard areas

| Area | Purpose |
| --- | --- |
| Memory management | Inspect, search, import, export, and delete long-term memories |
| Recall debugging | Enter a query and inspect returned memories and ranking |
| Graph view | Browse entities, relationships, and memory connections |
| System status | Review indexes, backups, statistics, and runtime status |

## What the graph view is good for

| Observation | Example |
| --- | --- |
| High-frequency entities | Users, projects, places, group topics |
| Stable relationships | "A person likes something", "a project depends on a technology" |
| Cross-memory links | The same entity appearing across multiple conversations |
| Aging risk | Low-importance relationships that have not been accessed for a long time |

::: tip
Dashboard operations reuse the plugin runtime MemoryEngine and GraphStore, so they do not bypass backend data safety logic.
:::

## Memory migration

The memory page supports JSON and CSV:

- Export downloads all memories when nothing is selected, or only the selected rows otherwise.
- Native JSON round-trips summaries, scopes, personas, structured metadata, and retained source messages.
- External JSON may use `content`, `text`, `summary`, or `memory` for summary text. Collections may use `memories`, `long_term_memories`, `short_term_memories`, or conversation maps keyed by session ID.
- Import always runs a preview first. By default it skips duplicates using summary content + session ID + persona ID; duplicates can be explicitly allowed.
- A source-only conversation without a summary makes one LLM call per imported item. Successful imports use the complete configured write pipeline: embeddings are regenerated, while graph and atom indexes are generated when those features are enabled and the imported data qualifies.

One file may contain up to 10,000 memories and must not exceed 50 MiB. Exports can contain sensitive source messages and should be protected like database backups.

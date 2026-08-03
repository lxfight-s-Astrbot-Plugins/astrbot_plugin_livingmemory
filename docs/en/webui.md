# WebUI

LivingMemory uses AstrBot official Plugin Pages for its dashboard. No extra web server is required.

## Entry

Open AstrBot WebUI:

`Plugins -> LivingMemory -> Pages -> dashboard`

AstrBot `4.24.2` or later is recommended. Older versions can still run the plugin, but the dashboard may be unavailable.

## Dashboard areas

| Area | Purpose |
| --- | --- |
| Memory management | Inspect and filter memories; edit summaries, topics, key facts, status, and importance; batch-delete; import and export |
| Recall debugging | Enter a query and inspect returned memories and ranking |
| Graph view | Browse entities, relationships, and memory connections |
| System status | Review active, archived, and deleted counts plus graph, atom, importance, and session statistics |
| Prompt management | Browse prompts by category, edit overrides, identify customized templates, and restore or load defaults |

## Memory details and lifecycle

- Editing a summary, topic, or key fact rebuilds that memory's embedding, BM25, graph, atom, and related derived data. Status-only or importance-only edits do not unconditionally rebuild every index.
- Memories above the source-retention threshold show source messages in the detail panel. When at least two source messages are available, the Dashboard can call the LLM to replace the memory with a new summary.
- Changing status to `archived` removes a memory from normal recall indexes while retaining its source document. Changing it back to `active` regenerates its embedding and derived indexes.
- Selection checkboxes support batch deletion from the current list. Export includes only selected memories when a selection exists.

::: warning
Deletion is permanent. Archive memories that may need to be restored later. Restoration requires a working embedding provider.
:::

## Prompt management

The prompt page shows each template's purpose, variables, and default or customized state. The editor can save an override, load default content for further editing, or remove an override and restore the built-in default. Templates marked as JSON must remain valid JSON or saving is rejected.

Prompt overrides live in the plugin data directory and do not modify repository templates, so they remain in place across plugin upgrades.

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

Background index checks and large rebuild progress are currently exposed through `/lmem status`. See [Commands](/en/commands#index-maintenance-states) for states and recovery guidance.

## Memory migration

The memory page supports JSON and CSV:

- Export downloads all memories when nothing is selected, or only the selected rows otherwise.
- Native JSON round-trips summaries, scopes, personas, structured metadata, and retained source messages.
- External JSON may use `content`, `text`, `summary`, or `memory` for summary text. Collections may use `memories`, `long_term_memories`, `short_term_memories`, or conversation maps keyed by session ID.
- Import always runs a preview first. By default it skips duplicates using summary content + session ID + persona ID; duplicates can be explicitly allowed.
- A source-only conversation without a summary makes one LLM call per imported item. Successful imports use the complete configured write pipeline: embeddings are regenerated, while graph and atom indexes are generated when those features are enabled and the imported data qualifies.

One file may contain up to 10,000 memories and must not exceed 50 MiB. Exports can contain sensitive source messages and should be protected like database backups.

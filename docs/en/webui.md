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

## Appearance

Open **Appearance** using the palette icon at the bottom of the sidebar (in the bottom navigation on mobile). Four visual styles apply immediately:

| Style | Design |
| --- | --- |
| Editorial | The original green grid, bold headlines and crisp lines |
| Studio | Indigo accents, rounded floating cards and horizontal graph metrics |
| Paper | Warm paper surfaces, serif headings, double rules and reading space |
| Terminal | Monospace type, cyan accents, sharp borders and graph metrics on the right |

Each style supports Light, Dark and Auto. Auto follows AstrBot, or your device when opened independently. An explicit color mode is not overridden by AstrBot. Preferences are saved in the current browser and survive reloads; when local storage is blocked, they apply only for the current visit. Options support keyboard selection; press Escape to close.

## Memory details and lifecycle

The memory page follows **filter → select → act**. Keyword and session inputs filter automatically; Enter or **Filter** applies all current controls immediately. **Reset filters** clears the criteria. Page size lives beside pagination, and Refresh is at the top right. Batch actions appear after selecting rows on the current page, with a Clear selection action.

Import and export live in a separate collapsible area with an explicit export scope: selected rows when a selection exists, otherwise **all memories regardless of list filters**. Import progresses through reading, preview, confirmation and writing, with visible feedback at each step; empty previews never proceed to a write. Related controls and page switching remain locked during bulk operations and transfers to prevent duplicate submissions or changing the operation's scope.

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

Memory and prompt editors ask how to handle unsaved changes before closing, cancelling or switching pages. **Keep editing** retains the draft; **Discard changes** exits. **Load default content** only fills the prompt editor until Save is clicked. The editor cannot switch to a different prompt while saving.

Recall supports Ctrl / Cmd + Enter. A new request clears previous results and displays failures explicitly, so old cards cannot be mistaken for the new query's output. Graph search and memory-ID inputs sit beside their corresponding actions, and session scope remains available on mobile.

## What the graph view is good for

| Observation | Example |
| --- | --- |
| High-frequency entities | Users, projects, places, group topics |
| Stable relationships | "A person likes something", "a project depends on a technology" |
| Cross-memory links | The same entity appearing across multiple conversations |
| Aging risk | Low-importance relationships that have not been accessed for a long time |

::: tip
The graph page shows a constrained subgraph of recent memories by default (node/edge caps apply). Click the **Full Graph** button to load every node and relation.
:::

In 2.7.0-beta.1, zoomed reading, hover and node selection pause ambient movement and stabilize label priority and collision bounds. Settled reading stops idle drawing; overview motion resumes when zoomed out with no hover or selection. Lower-priority labels may be omitted when space is limited; select a node to read its full details.

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

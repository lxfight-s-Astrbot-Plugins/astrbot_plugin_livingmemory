# WebUI

LivingMemory uses AstrBot official Plugin Pages for its dashboard. No extra web server is required.

## Entry

Open AstrBot WebUI:

`Plugins -> LivingMemory -> Pages -> dashboard`

AstrBot `4.24.2` or later is recommended. Older versions can still run the plugin, but the dashboard may be unavailable.

## Dashboard areas

| Area | Purpose |
| --- | --- |
| Memory management | Inspect, search, and delete long-term memories |
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

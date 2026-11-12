# Releases and Upgrades

## 2.12.0 · November 12, 2026

2.12.0 is a storage and recall-chain performance release: graph-route recall, node matching, scoped vector recall, atom search and graph writes were optimized end to end. On a copy of a 156 MB production database, database-side recall time drops from about 90 ms to 10-20 ms and the graph write cost per memory drops from 41 ms to 26-30 ms. Commands, configuration, injection format and database schema stay compatible, and no migration is required.

[Download 2.12.0](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.12.0)

### What changed

- **Faster recall**: the graph route filters archived memories through the indexed status expression and probes only matched nodes (73-78 ms -> 4-14 ms); 1-2 character CJK node matches go through a new bigram FTS index (13-19 ms -> 1.1 ms); scoped vector recall filters candidates with IDSelectorBatch before the ANN search (p50 22.1-22.9 ms -> 5.6-6.2 ms, rows fetched per query 3759 -> about 100); the atom miss path no longer scans content with LIKE (16.3 ms -> 0.4 ms).
- **Faster writes**: one memory's graph data (old graph deletion, node/edge/entry writes and orphan cleanup) is committed in a single transaction, so index_memory drops from 41 to 26-30 ms and connections per write from 7 to 3; indexing graph_entries.edge_id drops single-memory deletion from 489 to 17 ms and a batch of 20 from 7.6 s to 0.14 s.
- **Benchmark tooling**: new scripts/benchmark_storage_recall.py measures the graph read path, graph writes, index_memory and the atom fallback against a real database copy.

### Behavior changes

- No command, configuration, injection format or database migration changes; defaults stay the same.
- The first start after the upgrade backfills the graph node bigram FTS index (about 0.7 s for 10k nodes) and the database grows by about 2.5 MiB per 10k nodes; later starts skip it.
- Raw-format atom FTS keeps the LIKE fallback; after switching recall_engine.tokenizer, rebuild indexes as before.

### Verification

1606 backend tests and the recall quality golden set pass, Ruff check and format pass, no frontend changes, and both backend and frontend CI jobs pass on PR #433.

### Installing and upgrading

1. Stop the plugin and back up the full data directory, update to 2.12.0, then restart AstrBot and reopen the plugin views.
2. No database migration is required; the first start creates livingmemory_graph_nodes_bigram_fts and the graph_entries.edge_id index and backfills existing graph nodes.
3. Large stores spend a few extra seconds building the index on the first upgraded start (one-time cost); later starts have no extra overhead.

## 2.11.0 · November 12, 2026

2.11.0 extends what you can do with memory: an authenticated external memory API with an MCP bridge, versioned user profiles, request-level recall traces, a diagnostics page, channel public memory and an ordered LLM provider fallback chain, a Liquid Glass default appearance, and the complete full-graph view restored. New capabilities are conservative by default: the external API and channel memory are off and traces stay in memory. Public commands and existing configuration remain compatible, and no database migration is required.

[Download 2.11.0](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.11.0)

### What changed

- **External memory API**: a local HTTP interface for health, personas, recall, write (`idempotency_key`), single-memory read and soft archive; keys are stored as hashes with read/write permissions, persona and scope limits and per-minute rate limits, plus an audit log at `external_api_audit.jsonl`; includes `/lmem apikey` commands and a dependency-free MCP bridge (`search_memory`, `save_memory`, `list_personas`). Disabled by default.
- **User profiles**: structured `basic` / `preferences` / `status` / `task_preferences` profiles per scope and persona, with version history and pending candidates; the dashboard supports browsing, editing, deleting and reviewing, and injection is capped at 300 characters outside the recall budget.
- **Recall traces**: per-route candidates and latency, filter reasons and final injection size; inspect them with `/lmem trace` or the diagnostics page, together with memory lineage (source messages, supersede chain, graph links).
- **Diagnostics page**: one place for recall traces, channel memory, external API keys and audit, and LLM usage with the fallback chain.
- **Channel public memory**: group chats can enable a channel scope so group summaries land in a shared pool and group recall queries both scopes with guaranteed slots; disabled by default, with no data migration.
- **Provider fallback**: an ordered fallback chain and a per-provider call timeout, switching on timeout, rate limit, 5xx or auth failure; `/lmem status` and the diagnostics page report fallback counts.
- **Appearance and graph**: Liquid Glass is the new default (floating glass navigation, continuous corners, large 2D graph workspace) while all existing styles remain selectable; the full graph again includes archived memories and relations without truncation, restoring the 3D galaxy layout.
- **Fixes**: external API route dispatch, the POST alias for key revocation, the disabled state of the external API tab, responsive control icons and the full-graph load confirmation.

### Behavior changes

- The default appearance is now Liquid Glass; the previous Editorial style remains available under Appearance.
- User profiles are enabled by default and summaries generate profiles injected as up to 300 characters; turn off `profile_settings.enabled` to disable them.
- Recall traces are enabled by default but kept in memory (latest 50, no query text); enable `trace_settings.persist_enabled` for JSONL persistence.
- The full graph returns complete data again (archived memories and relations, no node or relation cap): this reverts the 2.10.0 behavior, so first loads on large stores are heavier; the confirmation and cancel steps remain.
- The external memory API and channel public memory are disabled by default; enabling the external API starts a local HTTP listener (default `127.0.0.1:7801`).
- New tables are created idempotently with `CREATE TABLE IF NOT EXISTS`; upgrades need no migration.

### Verification

Backend tests: 1,600 passed; frontend tests: 203 passed. Ruff, the dashboard artifact check (first-load gzip 80,297 B against an 81,920 B budget) and the VitePress build pass.

### Installing and upgrading

1. Stop the plugin and back up the full data directory, update to `2.11.0`, then restart AstrBot and reopen the plugin views.
2. No database migration is required; the first start creates `memory_profiles`, `memory_profile_history`, `external_api_keys` and `external_api_idempotency`.
3. Turn off `profile_settings.enabled` if profiles are not needed, and `trace_settings.enabled` if traces are not needed.
4. To let an external agent read and write memory, enable `external_api.enabled`, create a key with `/lmem apikey create`, and configure the MCP bridge as described in [External memory API](/en/external-api).
5. The full graph still asks for confirmation; prefer the default bounded subgraph for everyday browsing of large stores.

## 2.10.0 · November 9, 2026

2.10.0 fixes recall noise and the memory-retention policy, cuts summary and injection cost, adds data diagnostics, recall-quality evaluation and engineering gates, and fixes lost tool-call turns, unbounded full-graph loads and ever-growing backups. Several defaults changed; read "Behavior changes" before upgrading.

[Download 2.10.0](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.10.0)

### What changed

- **Recall**: default similarity gate 0.45, recent-memory slots off by default and a 3000/400 injection budget; reflection templates are ~70% smaller, so unrelated memories are no longer injected in bulk.
- **Lifecycle**: cleanup now archives by default (120 days / importance 0.25), with 0.005 decay and a 0.6 protection threshold; a supersede data path is available; failed parsing and low-quality summaries are no longer stored.
- **Cost and performance**: only transient errors retry, reflection concurrency is capped at 2 and writes reuse the dedup embedding; BM25/atom retrieval reuse read connections; Atom FTS, networkx and FAISS startup paths are fixed; an optional builtin tokenizer is available.
- **Ops and diagnostics**: new `/lmem diag` and `/lmem diag compact`; pages fail fast with a banner while unready; backups are pruned at startup (2 newest version backups, 512 MB total cap).
- **Graph and UI**: full graph excludes archived memories and caps at 5000 nodes / 20000 relations, with confirm, progress and cancel; first-load gzip −15.2%; settings and accessibility improvements.
- **Engineering**: main.py coverage 97%, backend coverage 86%; offline recall-quality evaluation plus coverage/docs CI gates; dependency upper bounds and a declared host version range.

### Behavior changes

- Low-importance memories are archived instead of deleted; recall-gate and recent-slot defaults changed and can be reverted through configuration.
- Full graph no longer shows archived memories and truncates above the caps; assistant text from tool-call turns is now stored.
- The first startup after upgrading prunes historical backups, so back up the full data directory first; switching to the builtin tokenizer requires a rebuild-index.
- Agentic recall defaults shrink to 3 calls / 6000 characters per request; an idle session gets one catch-up summary on its next turn after about 60 minutes.

### Verification

The complete suite passes 1,375 backend and 179 frontend tests with 86% coverage; Ruff, the dashboard bundle check, the VitePress build and docs checks pass.

### Install and upgrade

1. Stop the plugin and manually back up the full data directory (this version starts pruning old backups).
2. Update to `2.10.0`, restart AstrBot and reopen the plugin views; no database migration is required.
3. If recall becomes too sparse, set `recall_engine.min_similarity_for_retrieval` back to `0` and `recent_memory_count` back to `2`.
4. If you switch to the `builtin` tokenizer, run `/lmem rebuild-index` once; the default `jieba` needs no rebuild.
5. Run `/lmem diag` to inspect database, backup and index status; `/lmem diag compact` compacts the database file.

## 2.9.2 · November 9, 2026

2.9.2 consolidates the plugin's internal modules and fixes memory writes failing when SQLite WAL is unavailable on Docker Desktop shared mounts. Commands, configuration keys, page APIs, agent tools and the database schema are unchanged; no migration is required.

[Download 2.9.2](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.9.2)

### What changed

- **Docker/WAL fallback**: when the data directory lives on a Docker Desktop bind mount of a host disk (9p/drvfs, gRPC-FUSE, virtiofs) or a network share, SQLite cannot reliably map WAL's `-shm` shared-memory index, so new connections fail with `SQLITE_IOERR` (`disk I/O error`) and memory writes break. Startup now detects the mount type and probes WAL, safely converts existing WAL databases back to rollback journal when it is unavailable, and suggests moving the data directory; moving to a Docker named volume or a local disk restores WAL automatically. See [#398](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/398).
- **Connection policy**: journal mode is applied once per database at startup, and hot-path connections in BM25, atom, graph and conversation storage only set `busy_timeout`, preventing new connections from returning `database is locked` immediately when another commit is in flight.
- **Legacy platform import**: imported or migrated memories remap retired platform prefixes to the current platform identity, so old data remains recallable in session-filtered recall. See [#396](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/396).
- **Internal refactor**: merged split storage and processor modules, regrouped `MemoryEngine` by responsibility, reduced the initializer from three files to two, made page routes declarative, removed unused exports and dead code (about 1,849 production Python lines), moved document writes into the engine write transaction, added the Ruff gate to CI and lazy-loads the 3D graph scene. See [#396](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/396).

Contributed by [@lxfight](https://github.com/lxfight). The latest complete suite passed 1,210 backend and 150 frontend tests. New regressions cover filesystem WAL probing and fallback conversion, hot-path connection policy and legacy platform-prefix imports.

### Install and upgrade

1. Stop the plugin, back up its full data directory, update to `2.9.2`, then restart AstrBot and reopen Plugin Views. No database migration is required.
2. If the data directory is on a Docker Desktop host-disk bind mount or a network share, startup logs will report the rollback-journal fallback; move `/AstrBot/data` to a Docker named volume or a local disk to restore WAL.
3. Invalid configuration values now abort startup with `ConfigurationError` instead of being silently normalized; fix them before starting. Legacy injection-method values are still read, but settings expose only `extra_user_content`.
4. The database file-open failure `unable to open database file` in [#345](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/345) remains a separate issue from the `disk I/O error` handled here and stays open.

## 2.9.1 · November 5, 2026

2.9.1 fixes missing stable participant identities in memory injection and keeps synthetic cron and background-task wakeups out of real conversation memory. No database migration or configuration change is required.

[Download 2.9.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.9.1)

### What changed

- **Stable participant identities**: the injected `Participants` line now reads `participant_identities` and renders nickname#last4, so users sharing a nickname can be told apart and renamed users keep the same suffix. Only the last four characters of an account ID are exposed; bot identities render as “我” and memories without identity records keep the nickname list. Fixes [#388](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/388), see [#391](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/391).
- **Reflection source**: cron, background-task and plugin wakeups are no longer written to conversation history, so they do not enter the reflection window or change message counts, summary cursors and unsummarized rounds. Both newer `agent_wakeup` events and the `cron` platform used by 4.29.4 and earlier are skipped. Fixes [#389](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/389), see [#392](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/392).

Contributed by [@lxfight](https://github.com/lxfight). The latest complete suite passed 1,211 backend and 150 frontend tests. New regressions cover same-name and renamed participants plus both synthetic wakeup sources.

### Install and upgrade

1. Stop the plugin, back up its data directory and update to `2.9.1`, then restart AstrBot and reopen Plugin Views. No database migration or configuration change is required.
2. The fix applies to messages created after the upgrade; cron and background-task records already written to conversation history remain and can be cleaned up or re-summarized as needed.
3. The database file-open failure in [#345](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/345) remains open pending new diagnostic logs.

## 2.9.0 · November 3, 2026

2.9.0 adds adapter sharing and legacy ownership migration, with fixes for reflection backlog, history trimming and consolidation isolation. Existing vectors and document IDs are retained without regenerating embeddings.

[Download 2.9.0](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.9.0)

### What changed

- **Adapter sharing**: select sharing instances while retaining session, user-scope and persona restrictions. Recover identifiable legacy ownership; assign ambiguous records or transfer an old instance in settings, with SQLite backups before changes. See [#384](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/384).
- **Bounded reflection and retention**: each input has at most `max_messages_per_segment` messages, default 120, with continuous backlog processing within the existing segment budget. Enforce the `max_messages_per_session` hard cap even at cursor zero: remove summarized messages first, then the oldest unsummarized buffered messages if necessary, with a warning.
- **Cursors and consolidation**: coordinate snapshot reads with head trimming and commit by message ID/time so retries do not skip later segments. Consolidation and reflection persist mutually exclusively while LLM inference stays concurrent; session grouping also preserves personas and adapter ownership. See [#386](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/386).

Contributed by [@lxfight](https://github.com/lxfight). The latest complete suite passed 1,203 backend and 150 frontend tests. Validation covers adapter migration, real SQLite/FAISS, concurrent trimming and multiple Chrome viewport sizes.

### Install and upgrade

1. Stop the plugin and back up its full data directory. Raise `max_messages_per_session` or export history first if all unsummarized source messages must be retained; the hard cap can discard content not yet stored as memory.
2. Update to `2.9.0`, restart AstrBot and reopen Plugin Views. After configuring a sharing list, unselected and newly added instances are isolated; session isolation still separates actual sessions.
3. Ownership changes preserve `session_id`. Before a manual bulk transfer, pause affected adapters and wait for summaries to finish. Ambiguous legacy records need explicit assignment.
4. The database file-open failure in [#345](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/345) remains open pending new diagnostic logs.

## 2.8.4 · November 1, 2026

2.8.4 fixes authenticated 3D graph loading, improves SQLite open-error diagnostics and explains the host upgrade for timestamp write failures. No plugin configuration changes or database migration are required.

[Download 2.8.4](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.8.4)

### What changed

- **3D loading**: preload the module using the host-rewritten authenticated URL when the page opens. Create scenes on demand and release them on navigation. Fixes [#371](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/371); see [#378](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/378).
- **SQLite diagnostics**: failed access-statistics writes log the database path, native and extended error codes, error name and full traceback. See [#380](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/380). The remaining open failure in [#345](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/345) still needs the new logs to establish its cause.
- **Timestamp guidance**: upgrade AstrBot to 4.29.0 or later if an older host fails to write after SQLModel's datetime type change. See [#379](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/379) and upstream [#10211](https://github.com/AstrBotDevs/AstrBot/pull/10211).

These changes were contributed by [@lxfight](https://github.com/lxfight). Full feature PR CI passed, with the latest run covering 1,174 backend and 149 frontend tests. Chrome sandbox checks covered switching to 3D after token expiry, resource release and recreation.

### Install and upgrade

1. Retain data and configuration, update to `2.8.4`, restart AstrBot, then close and reopen the plugin view.
2. For timestamp write failures, upgrade the host to 4.29.0 or later as well. Regenerating embeddings is not required.
3. If all dashboard APIs report missing routes, confirm that `networkx` is installed in the runtime environment. The requirements already declare `networkx>=3.0`. From the AstrBot root, use the Python environment running AstrBot:

```bash
python -m pip install -r data/plugins/astrbot_plugin_livingmemory/requirements.txt
```

## 2.8.3 · November 1, 2026

2.8.3 is a maintenance release that fixes missing dashboard routes when dependencies are absent, prevents stale parent summaries from reintroducing expired atoms, and reduces browser memory use for large graphs. No configuration changes or database migration are required.

[Download 2.8.3](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.8.3)

### What changed

- **Dependencies and APIs**: declare direct Python dependencies, let AstrBot handle Page API import failures, and remove the branch that skipped failed route registration. See [#376](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/376), contributed by [@lxfight](https://github.com/lxfight).
- **Lifecycle**: ordinary and Agent recall inject the resolved live atoms, preventing parent summaries and key facts from reintroducing expired, forgotten or superseded content. See [#369](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/369), contributed by [@Ichooooooo](https://github.com/Ichooooooo).
- **Graph performance**: retain fewer edge fields, build indexes on demand, release GPU and Worker resources when leaving 3D, and remove a duplicate statistics request. Nodes without relation edges retain their source memories. See [#372](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/372), contributed by [@lxfight](https://github.com/lxfight).

### Validation

Merge validation passed in CI: 1,173 backend and 149 frontend tests. The original performance PR measured 2D JS heap after loading the same 10,068-node / 200,068-edge snapshot on one machine, decreasing from 144.1 MB to 47.0 MB. Results vary with hardware and data.

### Install and upgrade

1. Retain plugin data and configuration, update the code, restart AstrBot, then hard-refresh the dashboard and confirm version `2.8.3`.
2. If Python packages are still missing, run the following from the AstrBot root using the same Python environment that runs AstrBot, then restart:

```bash
python -m pip install -r data/plugins/astrbot_plugin_livingmemory/requirements.txt
```

3. The release includes offline frontend assets and does not require regenerating embeddings. Returning to a large 3D graph reruns its layout. For Git installations, use a clean working directory:

```bash
git fetch origin --tags
git switch --detach 2.8.3
```

## 2.8.2 · October 28, 2026

2.8.2 improves the 3D galaxy view with weighted communities, thick spiral arms, stable motion, layered relations and star dust, while fixing precise picking and detail expansion in large graphs. No configuration changes, database migration or new runtime dependencies are required.

[Download 2.8.2](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.8.2)

### What changed

- **Communities and shape.** Scoped weighted Louvain groups are positioned using real relationship strength. Large communities form thick arms, while small groups and isolates form clusters; branches and terminals fill continuous radial space.
- **Stable motion.** Worker geometry is preserved through slow system rotation and bounded local gravity. Hover/focus pauses that system's bulk rotation and resumes its saved state.
- **Relations and light.** Overview shows community curves, approaching reveals internal structure, and selection highlights direct relations. Full mode retains every edge. Dust is decorative; each valuable source prefers a summary node for its pulsar cue.
- **Interaction and tooling.** Picking follows screen positions and details follow the inspected center. A manual generator defaults to 10,000 nodes / 200,000 relations / 1,200 archived synthetic memories; repeated runs append more data.

### Verification

- Full CI passed 1,161 backend and 147 frontend tests; feature PR [#366](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/366) passed CI.
- Dashboard and documentation builds pass. Equivalent sandbox checks cover motion, actual picking, focus, zoom, mobile sizes, pause and visibility; details remain readable across eight appearance/theme combinations. A local synthetic 10,000-node/200,000-relation complete view reaches approximately 25 FPS; results vary by device and data.

### Install and upgrade

1. Keep existing data and configuration, download **Source code (zip)** from the release page, and replace the code.
2. Reload the plugin and hard-refresh, confirming version `2.8.2`. Backend community fields require the new frontend and backend to load together; select **Resume motion** if reduced motion is enabled.
3. Release archives contain offline frontend assets. Visual fixtures are appended only by manually running the script; select `livingmemory:visual:galaxy` to view existing fixtures as a full graph.
4. For a Git installation, switch a clean checkout to the tag:

```bash
git fetch origin --tags
git switch --detach 2.8.2
```

## 2.8.1 · October 28, 2026

2.8.1 improves the 3D memory graph's spatial layout, motion and reading interactions, adding hierarchical gravity, stellar coronas and high-value pulsars while fixing focus pivots and detail themes. No configuration changes, database migration or new runtime dependencies are required.

[Download 2.8.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.8.1)

### What changed

- **Gravity and hierarchy.** Memory types and real ties choose attractors, with a shared spin direction per system. Interior branches spread out and terminal nodes move closer, retaining depth and radial ordering throughout motion.
- **Stars and pulsars.** Representative nodes have coronas; facts and summaries associated with memories rated at least 8/10 show pulsing halos and rotating beams.
- **Focus and themes.** Rotation uses actual selected coordinates, with projection offsets reserving drawer space. Details, edit fields and mini graphs share a dark palette; returning to 2D restores the previous theme. Overview and selected relations are brighter.
- **Large graphs.** Gravity work grows linearly with node count; relations share positions without rebuilding their indices. Pause, hover/focus holds, reduced motion and background suspension are supported.

### Verification

- All 1,155 backend and 136 frontend tests pass; feature PR [#361](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/361) passed CI.
- Dashboard and documentation builds pass. Browser sandbox checks cover motion, focus, zoom, mobile sizes and visibility changes. A local synthetic 5,000-node/100,000-relation workload reaches approximately 26 FPS; results vary with real data and devices.

### Install and upgrade

1. Keep your existing data and configuration, download **Source code (zip)** from the release page, and replace the plugin code.
2. Reload the plugin and hard-refresh the dashboard, confirming version `2.8.1`. If reduced motion is enabled, select **Resume motion** in the 3D graph to enable animation explicitly.
3. Release archives include all offline frontend assets; using the plugin does not require Node.js. Run `npm ci` and `npm run build:dashboard` only after changing frontend sources.
4. For a Git installation, switch a clean checkout to the tag:

```bash
git fetch origin --tags
git switch --detach 2.8.1
```

## 2.8.0 · October 28, 2026

2.8.0 adds an independent warm-lit 3D graph. Clicking a node moves the camera closer and exposes its memories; search, memory focus and selection sharing work across 2D/3D. GPU batches and a separate layout Worker support large graphs, and full requests no longer truncate at fixed counts. No configuration changes or database migration are required.

[Download 2.8.0](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.8.0)

### What changed

- **Independent visuals and interaction.** Rotate, pan, zoom, focus nodes and browse related memories in a warm-lit space. The 3D view hides extra headings, statistics and repeated metadata.
- **Large graphs.** GPU batches draw nodes and indexed relations, a Worker computes the 3D layout, and rendering stops when settled or hidden. Toggle between structural overview and **All relations**.
- **Host compatibility.** An embedded Blob Worker works inside AstrBot's sandbox iframe and fixes Worker construction from origin `null`.
- **Community.** QQ group 1, 953245617, is full; the new group 2 is **1129994749**.

### Verification

- All 1,155 backend and 115 frontend tests pass; feature PR [#353](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/353) passed CI.
- Dashboard and documentation builds pass. Browser sandbox checks cover camera focus, touch input, memory focus and synthetic workloads up to 5,000 nodes / 100,000 relations. Results vary with real data and devices.

### Install and upgrade

1. Keep your existing data and configuration, download **Source code (zip)** from the release page, and replace the plugin code.
2. Reload the plugin and hard-refresh the dashboard, confirm version `2.8.0`, then select **3D Space** on the graph page.
3. Release archives include all offline frontend assets; using the plugin does not require Node.js. Run `npm ci` and `npm run build:dashboard` only after changing frontend sources.
4. For a Git installation, switch a clean checkout to the tag:

```bash
git fetch origin --tags
git switch --detach 2.8.0
```

## 2.7.3 · October 26, 2026

2.7.3 is a runtime fix release that resolves persistent SQLite write locks after failed transactions and reflection-window backlogs that could not be drained in one trigger. It also adapts to the asynchronous Provider and Plugin View APIs in AstrBot 4.27.3+. There are no new runtime dependencies or mandatory migration steps; as a stable release it participates in AstrBot's update prompts normally.

[Download 2.7.3](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.3)

### What changed

- **SQLite write-lock recovery**: Failed, cancelled or failed-to-commit write transactions now roll back immediately, and stale transactions are cleared before a connection is reused. This fixes an uncommitted write transaction holding a lock while other connections keep receiving `database is locked`. ([#348](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/348))
- **Reflection backlog drained in one run**: The new `reflection_engine.max_segments_per_run` option (default 10, range 1-50) lets one background task consume oldest split segments until the backlog is empty or the budget is reached, committing each segment's source window and cursor independently. ([#349](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/349))
- **AstrBot API adaptation**: AstrBot 4.27.3+ uses the asynchronous Provider API with a synchronous fallback on older releases; the dashboard prefers the Plugin View bridge while legacy Pages remains available. The minimum supported version is still 4.24.2. ([#350](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/350))

### Verification

- All 1,154 backend `pytest` tests and 110 frontend tests pass; CI is green.
- Dashboard bundles match their source and the VitePress documentation build passes; `git diff --check` passes.

### Upgrade notes

1. No configuration changes and no mandatory migration steps.
2. Sessions with many day or idle-gap splits can tune `reflection_engine.max_segments_per_run`; the default of 10 covers most deployments.
3. AstrBot 4.24.2 and later can upgrade directly: 4.27.3+ use the asynchronous Provider API and older releases fall back automatically.
4. `unable to open database file` is SQLite `SQLITE_CANTOPEN` and is not covered by this write-lock fix. If it persists after upgrading, provide the database path, directory permissions, disk state, and antivirus/sync-drive details.

### Install and upgrade

1. Stop AstrBot and back up the plugin code, plugin data directory and configuration.
2. Download **Source code (zip)** from the release page, extract it into `data/plugins/astrbot_plugin_livingmemory`, and keep existing data and configuration.
3. Start or reload AstrBot, confirm the plugin version reads `2.7.3`, then open `Plugins → LivingMemory → Views → dashboard`; older AstrBot versions label this entry as Pages.
4. For an existing Git installation, switch a clean checkout to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.3
```

## 2.7.2 · October 26, 2026

2.7.2 is a documentation patch that makes the embedding-model and free BGE-M3 setup available directly from the README and documentation site. It does not change runtime logic, add dependencies or require migration; as a stable release, it participates in AstrBot's update prompts normally.

[Download 2.7.2](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.2)

### What changed

- **Embedding-model primer**: A new Chinese and English "Embedding Models and BGE-M3" page explains text vectorization, semantic recall, and why indexing and querying must use the same model and dimensions.
- **SiliconFlow BGE-M3 setup**: The guide covers the free `BAAI/bge-m3` API key, API Base URL, model ID, 1024 dimensions and how to locate the AstrBot Provider ID.
- **README updates**: The Chinese, English and Russian README files gain a free embedding-model section and documentation link, so the repository homepage and plugin listing expose the full setup path.
- **Documentation navigation**: Quick Start, Configuration and the VitePress sidebar link to the new page, which is also covered by the Chinese/English parity test.

### Verification

- All 1,145 backend `pytest` tests pass; this release changes no runtime behavior.
- The VitePress documentation build passes; the README contract and Chinese/English docs-parity tests pass all 8 cases.

### Upgrade notes

1. No configuration changes and no mandatory migration steps.
2. Users who configured an Embedding Provider in 2.7.1 can upgrade directly; others can follow the new guide to use the free `BAAI/bge-m3`.
3. 2.7.2 was the current stable release when it shipped; earlier pre-releases remain available on the Releases page.

### Install and upgrade

1. Stop AstrBot and keep a backup of your existing plugin code, plugin data directory and configuration.
2. Download **Source code (zip)** from the release page, extract it into `data/plugins/astrbot_plugin_livingmemory`, and keep existing data and configuration.
3. Start or reload AstrBot, confirm the plugin version reads `2.7.2`, then open `Plugins → LivingMemory → Pages → dashboard`.
4. For an existing Git installation, switch a clean checkout to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.2
```

## 2.7.1 · October 22, 2026

2.7.1 is a patch release on top of 2.7.0 focused on configuration ergonomics and duplicate memories: advanced settings move from the AstrBot plugin config page to a dashboard Settings page (common panel plus five collapsed categories), and summaries gain write-time near-duplicate merging. No new runtime dependencies and no mandatory migration steps; as a regular release it participates in AstrBot's update prompts normally.

[Download 2.7.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.1)

### What changed

- **Shorter host config page.** The AstrBot plugin config page keeps only model providers (including the required Embedding Provider ID) and reply language; recall, reflection, graph, consolidation and other advanced options move to the dashboard.
- **Dashboard Settings page.** Eight common settings stay visible; the rest are folded into five categories (memory generation and lifecycle, recall and tools, graph and atoms, access and isolation, system and maintenance) with search, expand/collapse all and localized field labels.
- **Write-time near-duplicate merging.** Summaries in the same session/persona that reach the similarity threshold (0.92 by default) merge into the existing memory, keeping topics, key facts, atoms and source messages; failures fall back to a normal write.
- **Embedding Provider clarification.** The field is now documented as required; leaving it empty only falls back to the first available provider and logs a warning, avoiding broken vector/graph indexes.

### Verification

- Backend: 1,145 tests passed; frontend: 109 tests passed; CI green.
- Version-consistency and Chinese/English docs-parity guards pass; dashboard bundles match their sources.

### Upgrade notes

1. No configuration changes and no mandatory migration steps.
2. If no Embedding Provider ID is set, create and enable one in AstrBot's LLM settings first; otherwise vector/graph indexes may fail to initialize.
3. Reload the plugin after saving dashboard settings so options read during initialization take effect; runtime options such as duplicate merging apply immediately.
4. Run `/lmem rebuild-graph` to backfill graph data for older memories, and `/lmem rebuild-index` after changing the embedding model.
5. 2.7.1 was the stable release at the time; earlier pre-releases remain available on the Releases page.

### Install and upgrade

1. Stop AstrBot and keep a backup of your existing plugin code, plugin data directory and configuration.
2. Download **Source code (zip)** from the release page, extract it into `data/plugins/astrbot_plugin_livingmemory`, and keep existing data and configuration.
3. Start or reload AstrBot, confirm the plugin version reads `2.7.1`, then open `Plugins → LivingMemory → Pages → dashboard`.
4. For an existing Git installation, switch a clean checkout to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.1
```

## 2.7.0 · October 21, 2026

2.7.0 is the stable release that consolidates everything from `2.7.0-beta.1` through `beta.4`. The rebuilt dashboard, recall-quality work, standardized injection, lifecycle guarantees, hot-path performance and Japanese support all reach the stable channel; as a non-prerelease it participates in AstrBot's plugin update prompts normally. No new runtime dependencies and no mandatory migration steps; existing configuration keeps working.

[Download 2.7.0](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0)

### What's included

- **Dashboard:** memory provenance and write reason, per-route recall scores, graph and memory list linked both ways, retryable failure states, and four visual styles.
- **Recall quality:** score-weighted fusion, wider candidate pools, cross-route agreement bonus, CJK character bigrams in BM25, and atom-lifecycle filtering.
- **Consistency:** injection is add-only temporary parts; WebUI edit/retry rollback is reconciled, summary cursors and revisions commit atomically, and failed writes are compensated.
- **Lifecycle:** summary windows split by day or idle gap, type-specific atom TTLs and decay, consolidation rollback, restorable archives, and pre-migration backups.
- **Performance and indexing:** the recall hot path no longer blocks on FAISS persistence, the write-operation journal is gone (one memory write drops from up to eight transactions to two), dashboard assets are bundled and trimmed, and index maintenance runs in the background with shadow generations.
- **Engineering and localization:** CI pins a verified host revision, test dependencies are locked, version and documentation guards exist, and Japanese is supported across replies, the config page and the dashboard.

### Verification

- Backend `pytest`: 1,132 passed; frontend: 107 passed; CI runs against the pinned host revision.
- Chinese and English docs pass the parity guard; dashboard bundles match their sources.

### Upgrade notes

1. No configuration changes are required; new settings have defaults.
2. The first startup after upgrading fills in index and version information automatically; running `/lmem rebuild-index` once is recommended so older memories benefit from the retrieval improvements.
3. Recall defaults changed: `fusion_mode=score`, `fusion_bm25_weight=0.5`, `recall_candidate_multiplier=3`, `fusion_agreement_bonus=0.25`; set `fusion_mode=rrf` to keep the previous rank fusion.
4. The plugin requires AstrBot 4.24.2 or later; `@register` is removed and metadata comes from `metadata.yaml`.
5. Dashboard preferences (theme, colour mode, language) are stored in the browser and survive the upgrade.

### Release timeline

| Release | Focus |
| --- | --- |
| 2.7.0-beta.1 | Dashboard visual rebuild, stable graph reading, editing and import workflows |
| 2.7.0-beta.2 | Standardized add-only injection and removal of history rewriting |
| 2.7.0-beta.3 | Visible recall provenance, score fusion and CJK bigrams, agentic recall beta |
| 2.7.0-beta.4 | Hot-path performance, consistency hardening, architecture cleanup, Japanese and lighter assets |

### Install and upgrade

1. Stop AstrBot and keep a backup of your existing plugin code, plugin data directory and configuration.
2. Download **Source code (zip)** from the release page, extract it into `data/plugins/astrbot_plugin_livingmemory`, and keep existing data and configuration.
3. Start or reload AstrBot, confirm the plugin version reads `2.7.0`, then open `Plugins → LivingMemory → Pages → dashboard`.
4. For an existing Git installation, switch a clean checkout to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.0
```

## 2.7.0-beta.4 · October 21, 2026

Maintenance pre-release for the 2.7 series covering PRs 308–332 (25 merged pull requests): recall and write hot-path performance, data-consistency hardening, responsibility cleanup across the codebase, and a lighter dashboard with Japanese support. No new runtime dependencies and no mandatory migration steps; existing configuration keeps working and the stable channel remains 2.6.1.

[Download 2.7.0-beta.4](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.4)

### What changed

- **Faster recall.** FAISS persistence now uses a readers-writer lock, so searches no longer wait for full-index flushes; LLM hooks stop polling for initialization; startup index reads move off the event loop; graph recall gains the reverse-edge index and reuses read connections; access statistics are coalesced; tokenizer caching and vector-candidate reuse remove duplicate work.
- **Cheaper writes.** The write-operation journal and its repair chain are gone (about 930 fewer lines), reducing a single memory write from up to eight transactions to two; batch deletes commit at the FTS boundary to avoid `database is locked`.
- **Steadier data.** Semantic consolidation is grouped by `(session_id, persona_id)` and rolls back the archive on failure; daily decay updates only changed keys and no longer records a date marker on failure; migrations require a backup first and restore it on failure; atom FTS gets a backfill.
- **More reliable indexing.** Rebuild retries use plain exponential backoff instead of guessing rate limits from error text; when the host cannot delete vectors, archiving marks `needs_index_rebuild` so the startup check rebuilds and removes ghost vectors.
- **Lighter dashboard.** The full lucide library is replaced by a 21-icon subset (about 408 KB less); styles and scripts are bundled and minified, cutting blocking requests from 14 to 5 and CSS from 85.9 KB to 69.2 KB; cascade-safe CSS rules are de-duplicated.
- **Cleaner architecture and engineering.** Write-side index maintenance moves to `MemoryIndexWriter`; the reflection, `add_memory` and graph-snapshot methods are split into stages; version and index DDL are single-sourced; CI pins a verified host commit; test dependencies are locked; the deprecated `@register` decorator is removed; the dashboard and config page support Japanese.

### Verification

- Backend `pytest`: 1,132 passed; frontend: 107 passed; CI passes against the pinned host revision.
- New documentation-parity and version-consistency guards prevent future drift between languages and version sources.

### Upgrade notes

1. No configuration changes are required; new settings have defaults.
2. The first startup after upgrading fills in indexes and version information automatically; run `/lmem rebuild-index` once if the existing index is older.
3. The plugin requires AstrBot 4.24.2 or later; after the `@register` removal the host discovers the `Star` subclass automatically.
4. Dashboard preferences (theme, colour mode, language) are stored in the browser and survive the upgrade.

### Highlighted pull requests

| PR | Change | Author |
| --- | --- | --- |
| [#308](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/308) | Unblock recall hot path from FAISS persistence | [@lxfight](https://github.com/lxfight) |
| [#309](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/309) | Graph-route recall I/O and timing-test stability | [@lxfight](https://github.com/lxfight) |
| [#310](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/310) | Consolidation, decay and migration reliability | [@lxfight](https://github.com/lxfight) |
| [#316](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/316) | Remove write-operation journal and repair chain | [@lxfight](https://github.com/lxfight) |
| [#317](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/317) | Replace full lucide library with a 21-icon subset | [@lxfight](https://github.com/lxfight) |
| [#319](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/319) | Bundle and minify dashboard assets | [@lxfight](https://github.com/lxfight) |
| [#321](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/321) | Remove deprecated `@register`, pin CI host | [@lxfight](https://github.com/lxfight) |
| [#323](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/323) | Split the 343-line reflection handler | [@lxfight](https://github.com/lxfight) |
| [#328](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/328) | Japanese for bot replies and config page | [@lxfight](https://github.com/lxfight) |
| [#329](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/329) | Japanese dashboard and language menu | [@lxfight](https://github.com/lxfight) |

### Install and upgrade

1. Stop AstrBot and keep a backup of your existing plugin code, plugin data directory and configuration.
2. Download **Source code (zip)** from the beta release page, extract it into `data/plugins/astrbot_plugin_livingmemory`, and keep existing data and configuration.
3. Start or reload AstrBot, confirm the plugin version reads `2.7.0-beta.4`, then open `Plugins → LivingMemory → Pages → dashboard`.
4. For an existing Git installation, switch a clean checkout to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.4
```

## 2.7.0-beta.3 · October 20, 2026

Pre-release of the 2.7 series. This build covers two areas: the dashboard now reports where memories come from and why a recall result surfaced, and the recall pipeline fuses text routes by normalized score, searches wider candidate pools and indexes CJK character bigrams in BM25. An opt-in agentic recall beta is also available. There are no new runtime dependencies and no mandatory migration steps; the stable channel remains 2.6.1.

[Download 2.7.0-beta.3](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.3)

### What changed

**Dashboard**

- Every memory is labelled with how it was written — agent write, import, re-summarization or automatic summarization — and summaries whose quality is unusually low are flagged. The detail panel adds the source, the agent's `memorize_reason` and, for edited content, the ID of the memory it replaced.
- The recall test page lists the retrieval routes that matched each result (document keyword, document vector, graph keyword, graph vector) with their individual scores, and echoes the `k` and session filter used for the request.
- Graph and memory list are linked in both directions: a node lists its associated memories and opens them directly, and memory details offer **Locate in graph**. Node details are non-modal, so several nodes can be compared in sequence.
- Request failures on the system and prompt pages stay on the page with a retry action instead of disappearing with a transient toast, and the standing explanatory copy was removed in favour of control state and feedback.

**Recall quality**

- Document-route fusion moved from rank-based equal weighting to per-query normalized scores (`fusion_mode=score`, `fusion_bm25_weight=0.5`). Each route now contributes `k × 3` candidates before fusion, and documents returned by both routes at close ranks receive a consensus bonus (`fusion_agreement_bonus=0.25`).
- BM25 indexes and queries character bigrams of contiguous CJK runs in addition to jieba tokens, so short queries and proper nouns (people, shops, model numbers) no longer miss because index-side and query-side segmentation differ. English and numeric tokens are unchanged.
- Measured with the shipped defaults, end-to-end through `MemoryEngine.search_memories()` at `k = 10`: Chinese benchmark (1,211 queries) MRR `0.86 → 0.95`, Hit@1 `0.79 → 0.93`; English LoCoMo (1,536 evidence-grounded questions) MRR `0.66 → 0.73`, Hit@1 `0.53 → 0.62`. The Chinese set is synthetic; LoCoMo stores raw session transcripts, so its absolute scores understate production recall.

**Agentic recall (opt-in beta)**

- Enabling `agent_tools.enable_agentic_recall_beta` (requires `agent_tools.enable_recall_tool`) lets the model run multi-turn recall within one request: `recall_long_term_memory` gains people-centric search, event-time ranges, exclusion of candidates already seen and cursored pagination, and the new `read_memory_evidence` tool reads a memory's active facts and its retained source text (when the whole source is visible to the session) by memory ID, resuming from a cursor after truncation.
- One per-request budget caps tool calls (`agentic_recall_max_calls`, default 4), cumulative tool time (`agentic_recall_time_budget_seconds`, default 20) and per-call output (`agentic_recall_max_result_chars`, default 12,000). When the budget is spent the model answers from the evidence it already collected.
- The recall rule prompt is injected add-only as a temporary part, so it never enters conversation history or the plugin's message store. With the switch off, tool registration, retrieval and prompts are identical to 2.7.0-beta.2.

**Also included**

- Admin-only forget tool (`agent_tools.enable_forget_tool`, off by default): archives memories instead of deleting them; an administrator can restore them from the dashboard. Per-call count and per-session cooldown are enforced.
- `/lmem migrate-scope <target> [apply] [source]`: explicit memory scope migration, dry-run by default, with an automatic database backup before `apply`.
- `provider_settings.llm_stream_mode` (`auto`/`always`/`never`): makes providers that only accept streaming requests usable for memory processing.
- Summary windows split by local day and idle gap; message times use the platform timestamp. The conversation store reconciles with WebUI edit/regenerate and retry rollbacks.
- FAISS writes fall back to an ASCII-only temporary directory on non-ASCII paths; multi-line JSON parsing and summarization prompt prefix caching are fixed.
- Dashboard rendering, touch input and edit-race fixes; `vue` 3.5.44 and `source-map-js` 1.2.2 security updates; zh-CN and ja-JP plugin localization.

### Upgrade notes

- Existing configuration needs no changes; new parameters have defaults.
- Recall defaults changed: `fusion_mode=score`, `fusion_agreement_bonus=0.25`, `recall_candidate_multiplier=3`. Set `fusion_mode: rrf` to keep the previous rank fusion.
- Run `/lmem rebuild-index` once if existing memories should benefit from bigram tokens; new writes pick them up automatically and recall stays available during the rebuild.
- Dashboard preferences (theme, colour mode, language) are stored in the browser and survive the upgrade.

### Known limitations

- In **Full Graph** mode the node panel does not list related memories (that snapshot does not return entry data; limited overview and query modes are unaffected).
- The Chinese evaluation set is model-generated and is intended for comparing retrieval designs, not for estimating live traffic distribution.
- Agentic recall is a beta: its tool-use adoption and end-to-end behaviour with real models are still being validated, and the switch is off by default.

### Highlighted pull requests

| PR | Change | Author |
| --- | --- | --- |
| [#274](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/274) | Opt-in agentic recall beta: multi-turn recall, evidence reading and shared per-request budget | [@Sisyphbaous-DT-Project](https://github.com/Sisyphbaous-DT-Project) |
| [#281](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/281) | Fix dashboard rendering, touch input, layout and edit races | [@lxfight](https://github.com/lxfight) |
| [#299](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/299) | Show retrieval route scores in the recall test page | [@lxfight](https://github.com/lxfight) |
| [#300](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/300) | Surface memory origin and why it was remembered | [@lxfight](https://github.com/lxfight) |
| [#301](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/301) | Link the graph and the memory list in both directions | [@lxfight](https://github.com/lxfight) |
| [#302](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/302) | Make graph encodings self-consistent and stable | [@lxfight](https://github.com/lxfight) |
| [#303](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/303) | Unify request feedback and correct system/prompt page semantics | [@lxfight](https://github.com/lxfight) |
| [#304](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/304) | Remove dead i18n keys, overridden styles and decoration | [@lxfight](https://github.com/lxfight) |
| [#305](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/305) | Fuse BM25 and vector results by normalized scores with wider candidates | [@lxfight](https://github.com/lxfight) |
| [#306](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/306) | Reward cross-route agreement in fused ranking | [@lxfight](https://github.com/lxfight) |
| [#307](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/307) | Index CJK character bigrams in BM25 | [@lxfight](https://github.com/lxfight) |

The complete list, including fixes and dependency updates not highlighted here, is in `CHANGELOG.md`.

### Install and upgrade

1. Stop AstrBot and keep a backup of the plugin code, its data directory and configuration.
2. Download **Source code (zip)** from the release above, unpack it into `data/plugins/astrbot_plugin_livingmemory`, and keep the existing data and configuration instead of replacing them with empty folders.
3. Start or reload AstrBot, confirm the version reads `2.7.0-beta.3` in the plugin list, then open `Plugins → LivingMemory → Pages → dashboard`.
4. To let existing memories benefit from the bigram tokens, send `/lmem rebuild-index` (it runs in batches while recall stays available).

With Git-managed plugin code, switch the clean plugin worktree to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.3
```

## 2.7.0-beta.2 · September 16, 2026

This prerelease covers the refactors and behavioral standardization since `2.7.0-beta.1`. Stay on 2.6.1 for the stable channel.

[Download 2.7.0-beta.2](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.2) · [Stable 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1) · [Full comparison](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/compare/2.7.0-beta.1...2.7.0-beta.2)

### What's new

- **Standardized memory injection — the plugin no longer touches host conversation history:** to stay compatible with AstrBot's architecture and its future technical direction, this release narrows the plugin's interactions with the LLM request pipeline and removes non-standard operations that exceeded the plugin's responsibilities. Memory injection is now exclusively an add-only write through AstrBot's official extension point: memories are appended to the current user message as a `mark_as_temp` temporary part that AstrBot filters out when saving conversation history. Injection never writes into conversation history and never affects the prefix cache.
- **Removed history format normalization:** the plugin no longer rewrites the content structure of history messages; both content-parts lists and plain strings are preserved exactly as AstrBot stored them.
- **Deprecated non-append injection modes:** `user_message_before`, `user_message_after`, `fake_tool_call` and `fake_tool_call_deepseek_v4` (plus the previously deprecated `system_prompt`) are deprecated; configured values automatically fall back to `extra_user_content` with a logged warning, no manual configuration changes required.
- **Legacy residue cleanup retained:** `auto_remove_injected` only removes injection fragments left in history by legacy injection modes from the request context; it never touches the user's own history messages.
- **Internal refactors (#268–#272):** dead code removal, page API and consolidation routed through the MemoryEngine public API, deduplicated JSON helpers and consolidated schema DDL, recall scope fallback aligned with write-side semantics, and Mixin host contracts declared as class annotations.

### Upgrade notes

- Legacy injection settings need no manual adjustment — they fall back automatically at runtime. To purge text-form injection residue left in stored history, run `/lmem cleanup` (fake tool-call message pairs are removed from context automatically on every request).

### Complete PR list and authors

| PR | Change | Author |
| --- | --- | --- |
| [#268](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/268) | Remove dead code paths and unused exports | [@lxfight](https://github.com/lxfight) |
| [#269](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/269) | Route page API and consolidation through the MemoryEngine public API | [@lxfight](https://github.com/lxfight) |
| [#270](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/270) | Dedupe JSON helpers and consolidate schema DDL into one source | [@lxfight](https://github.com/lxfight) |
| [#271](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/271) | Align recall scope fallback with write-side semantics | [@lxfight](https://github.com/lxfight) |
| [#272](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/272) | Declare mixin host contracts as class annotations | [@lxfight](https://github.com/lxfight) |
| [#273](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/273) | Standardize memory injection as add-only; version 2.7.0-beta.2 | [@lxfight](https://github.com/lxfight) |

### Install and upgrade

1. Stop AstrBot and keep a backup of your existing plugin code, plugin data directory and configuration.
2. Download **Source code (zip)** from the [2.7.0-beta.2 release](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.2). Extract the plugin into `data/plugins/astrbot_plugin_livingmemory`, without nesting another versioned directory inside it. Preserve existing plugin data and configuration.
3. Start or reload AstrBot, confirm `2.7.0-beta.2` in the plugin list, then open `Plugins → LivingMemory → Pages → dashboard`.
4. Legacy injection settings fall back automatically; run `/lmem cleanup` if you need to purge text-form injection residue left in stored history.

For an existing Git installation, switch a clean plugin checkout to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.2
```

GitHub marks this version **Pre-release**; use the specific tag. `releases/latest` still points to the stable release.

## 2.7.0-beta.1 · September 7, 2026

This prerelease includes all merged changes since stable **2.6.1**. Stay on 2.6.1 for the stable channel, or install the exact beta tag to try the new dashboard and recall fixes.

[Download 2.7.0-beta.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.1) · [Stable 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1) · [Full comparison](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/compare/2.6.1...2.7.0-beta.1)

### What's new

- **Four styles:** Editorial, Studio, Paper and Terminal each support light, dark and automatic modes, with preferences saved in the current browser.
- **Steadier graph reading:** zoom, hover and selection pause ambient movement. Stable label priority, collision bounds and bounded fonts reduce flicker; settled inspection stops idle rendering.
- **Clearer workflows:** filter, select, then act. Transfers have their own section, explicit export scope and import preview/confirmation. Editors protect unsaved changes and prevent duplicate saves or switching targets during a write.
- **More reliable recall and recovery:** newly written long memories retain full text; scope filters run before result limits; route failures are isolated; cached recall still checks atom expiry; graph rebuild rollback preserves pending deltas.
- **Less repeated work:** dual-route queries share an embedding, graph source documents load in batches, and FAISS search runs off the event loop. The dashboard coalesces duplicate requests and scroll renders, with retryable failure states.

Previously truncated text cannot be reconstructed by this upgrade. Atom-backed graph recall includes only facts that are still active in their lifecycle.

### Complete PR list and authors

The range starts at Git tag `2.6.1`. Credits below use each PR's verified GitHub author.

| PR | Change | Author |
| --- | --- | --- |
| [#259](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/259) | Recall, long-text storage and index recovery fixes; dashboard loading and mobile improvements | [@lxfight](https://github.com/lxfight) |
| [#260](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/260) | Four switchable dashboard styles | [@lxfight](https://github.com/lxfight) |
| [#261](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/261) | Stable graph labels during zoomed inspection | [@lxfight](https://github.com/lxfight) |
| [#262](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/262) | Filtering, batch actions, import and editing workflows with concurrency guards | [@lxfight](https://github.com/lxfight) |
| [#263](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/263) | Beta publication, complete PR credits, README redesign and VitePress documentation | [@lxfight](https://github.com/lxfight) |

### Install or upgrade

1. Stop AstrBot and keep a backup of your existing plugin code, plugin data directory and configuration. Appearance preferences are stored separately in the browser.
2. Download **Source code (zip)** from the beta release above. Extract the plugin into `data/plugins/astrbot_plugin_livingmemory`, without nesting another versioned directory inside it. Preserve existing plugin data and configuration.
3. Start or reload AstrBot and wait for dependencies and initialization. Confirm `2.7.0-beta.1` in the plugin list, then open `Plugins → LivingMemory → Pages → dashboard`.
4. Refresh the dashboard, using a hard refresh if the old interface remains. Follow [Verify the setup](/en/guide/getting-started#verify-the-setup) to check status, writes and recall.

For an existing Git installation, switch a clean plugin checkout to the specific tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.1
```

GitHub marks this version **Pre-release**. Use the specific tag: `releases/latest` still points to stable. Plugin Pages requires AstrBot 4.24.2 or later.

### What to test and report

Try theme/mode switching and persistence, zoomed dense graphs, combined filters, cancelling import previews, unsaved draft protection, and real-provider recall across sessions and long-text writes. [Report issues](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/new/choose) with plugin/AstrBot versions, theme, browser, reproduction steps and redacted logs.

The suite passes 785 Python tests and 55 frontend tests, plus VitePress builds and browser checks using mocked AstrBot APIs. These checks do not replace validation with real providers and your data.

### Return to stable

Stop AstrBot, restore the plugin code from tag `2.6.1`, then start AstrBot. To also return data to its pre-upgrade state, restore the matching data and configuration backup while AstrBot is stopped. Memories created after that backup will not be present in it. Do not replace database files while AstrBot is running.

## Earlier releases

See the full [CHANGELOG](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/blob/master/CHANGELOG.md) and [GitHub Releases](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases).

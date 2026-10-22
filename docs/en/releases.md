# Releases and Upgrades

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
5. 2.7.1 is the current stable release; earlier pre-releases remain available on the Releases page.

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

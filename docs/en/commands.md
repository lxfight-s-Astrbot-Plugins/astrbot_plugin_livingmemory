# Commands

LivingMemory commands use the `/lmem` prefix.

| Command | Description |
| --- | --- |
| `/lmem status` | Show memory statistics and, while maintenance is active or abnormal, its state, progress, and message |
| `/lmem search <query> [k]` | Search long-term memories; `k` defaults to 5 |
| `/lmem forget <id>` | Delete a specific memory |
| `/lmem rebuild-index` | Rebuild document indexes |
| `/lmem rebuild-graph` | Rebuild and compact graph indexes into memory-level vectors |
| `/lmem webui` | Show WebUI entry information |
| `/lmem summarize [message_count]` | Summarize now; with a count, re-summarize the most recent N messages |
| `/lmem reset` | Reset current session memory context |
| `/lmem cleanup [preview\|exec]` | Clean old memory injection fragments from message history |
| `/lmem help` | Show help |

## Index maintenance states

Startup consistency checks and automatic repairs run in the background. Use `/lmem status` to inspect non-idle states:

| State | Meaning | Recommended action |
| --- | --- | --- |
| `checking` | Document, vector, BM25, and graph consistency is being checked | No action is required; the plugin remains available |
| `rebuilding` | Indexes are being rebuilt in batches and progress is available | Keep providers available and do not start duplicate rebuilds |
| `partial` | Rebuild finished with tolerated failures or the final check still found differences | Check provider limits and logs, then run `/lmem rebuild-index` again if needed |
| `failed` | Background maintenance failed; the live index remains in use when a shadow generation did not switch | Fix the reported environment problem and rerun the rebuild |
| `cancelled` | Maintenance was cancelled during plugin stop or reload | The next startup checks again, or rebuild manually after the runtime is stable |

`idle` and `ready` do not add a maintenance section to the status reply.

## Troubleshooting

| Symptom | Try this |
| --- | --- |
| Recently discussed content is not searchable | Run `/lmem summarize` to ensure it has been written into long-term memory |
| A summary is empty or misses details | Use `/lmem summarize 20` for the latest 20 messages, or re-summarize retained source from memory details |
| Memories leak across personas | Check `filtering_settings.use_persona_filtering` |
| Group context is incomplete | Check `session_manager.enable_full_group_capture` |
| Search indexes look inconsistent | Run `/lmem rebuild-index`; for graph issues, run `/lmem rebuild-graph` |
| Old recall degrades after changing the embedding model | Wait for the provider-fingerprint check to trigger a full vector rebuild and monitor `/lmem status` |
| An error names `faiss-cpu 1.14.2` or a core dependency conflict | AstrBot 4.27.1 and the plugin require `faiss-cpu>=1.14.3`; update or repair the Desktop embedded environment and lock instead of overriding a core dependency |
| FAISS reports `Illegal instruction` | The plugin first probes generic instruction mode; if that also fails, install a wheel compatible with the current CPU and Python or move to a compatible runtime |
| FAISS reports an undefined `SuperKMeans` | Python wrappers and the binary extension are mismatched; cleanly reinstall a compatible build in the exact Python environment used by AstrBot |

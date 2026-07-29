# Commands

LivingMemory commands use the `/lmem` prefix.

| Command | Description |
| --- | --- |
| `/lmem status` | Show memory store status |
| `/lmem search <query> [k]` | Search long-term memories; `k` defaults to 5 |
| `/lmem forget <id>` | Delete a specific memory |
| `/lmem rebuild-index` | Rebuild document indexes |
| `/lmem rebuild-graph` | Rebuild and compact graph indexes into memory-level vectors |
| `/lmem webui` | Show WebUI entry information |
| `/lmem summarize [message_count]` | Summarize now; with a count, re-summarize the most recent N messages |
| `/lmem reset` | Reset current session memory context |
| `/lmem cleanup [preview\|exec]` | Clean old memory injection fragments from message history |
| `/lmem help` | Show help |

## Troubleshooting

| Symptom | Try this |
| --- | --- |
| Recently discussed content is not searchable | Run `/lmem summarize` to ensure it has been written into long-term memory |
| A summary is empty or misses details | Use `/lmem summarize 20` for the latest 20 messages, or re-summarize retained source from memory details |
| Memories leak across personas | Check `filtering_settings.use_persona_filtering` |
| Group context is incomplete | Check `session_manager.enable_full_group_capture` |
| Search indexes look inconsistent | Run `/lmem rebuild-index`; for graph issues, run `/lmem rebuild-graph` |

# Commands

LivingMemory commands use the `/lmem` prefix.

| Command | Description |
| --- | --- |
| `/lmem status` | Show memory store status |
| `/lmem search <query> [k]` | Search long-term memories; `k` defaults to 5 |
| `/lmem forget <id>` | Delete a specific memory |
| `/lmem rebuild-index` | Rebuild document indexes |
| `/lmem rebuild-graph` | Rebuild graph memory indexes |
| `/lmem webui` | Show WebUI entry information |
| `/lmem summarize` | Summarize the current session immediately |
| `/lmem reset` | Reset current session memory context |
| `/lmem cleanup [preview\|exec]` | Clean old memory injection fragments from message history |
| `/lmem help` | Show help |

## Troubleshooting

| Symptom | Try this |
| --- | --- |
| Recently discussed content is not searchable | Run `/lmem summarize` to ensure it has been written into long-term memory |
| Memories leak across personas | Check `filtering_settings.use_persona_filtering` |
| Group context is incomplete | Check `session_manager.enable_full_group_capture` |
| Search indexes look inconsistent | Run `/lmem rebuild-index`; for graph issues, run `/lmem rebuild-graph` |

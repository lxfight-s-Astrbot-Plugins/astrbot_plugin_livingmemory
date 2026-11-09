# External Memory API

The external memory API lets agent terminals such as Codex, WorkBuddy and Qoder read and write long-term memories over HTTP. It is disabled by default; when enabled, the plugin starts a dedicated loopback HTTP listener and ships a dependency-free MCP bridge.

> The external API reuses the same `MemoryEngine` path as in-chat writes: dedup, embeddings, graph memory and memory atoms all keep working. API keys are stored hashed, and the plaintext token is shown only once.

## Enable

1. Turn on `external_api.enabled` in the "External memory API" group of the AstrBot plugin configuration and adjust the listen address or port if needed.
2. Reload the plugin and confirm the log reports that the external memory API is listening.
3. Create an API key with the chat command or the dashboard (the token is returned only once).

```bash
# Loopback-only by default; expose it through a reverse proxy if needed
127.0.0.1:7801
```

| Setting | Description |
| --- | --- |
| `external_api.enabled` | Master switch, off by default; every external endpoint returns 404 while off |
| `external_api.listen_host` | Listen address, default `127.0.0.1` |
| `external_api.listen_port` | Listen port, default `7801` |
| `external_api.max_request_bytes` | Request body limit, default 256 KiB |
| `external_api.default_rate_limit_per_min` | Default requests per minute for new keys |
| `external_api.default_write_limit_per_min` | Default writes per minute for new keys |
| `external_api.audit_retention_days` | Audit log retention in days |
| `external_api.trust_proxy_headers` | Whether to trust `X-Forwarded-For` for the audit address |

## Create API keys

```text
/lmem apikey create workbuddy write
/lmem apikey create codex read p1,p2 livingmemory:global 120
/lmem apikey list
/lmem apikey revoke workbuddy
```

- The first argument is a unique name; the second is `read` or `write` (write also grants read).
- The optional third and fourth arguments are comma-separated allowed personas and scopes.
- The optional fifth argument overrides the per-minute request limit for that key.
- Host-authenticated Page API calls are also available: `GET /page/external/keys`, `POST /page/external/keys`, `DELETE /page/external/keys/<key_id>`.

## Endpoints

Every request uses `Authorization: Bearer <token>`; successful responses carry `status` `ok`, failures return `{status:"error", code, message}`.

| Method and path | Description |
| --- | --- |
| `GET /page/external/health` | Plugin version and readiness; requires read scope |
| `GET /page/external/personas` | Available personas and scopes; requires read scope |
| `POST /page/external/recall` | Recall memories with `{query, k?, persona_id?, scope?}` |
| `POST /page/external/memories` | Write a memory, optionally with `idempotency_key` |
| `GET /page/external/memories/<memory_id>` | Read one memory; requires read scope |
| `POST /page/external/memories/<memory_id>/archive` | Soft-archive one memory; requires write scope |

Status codes: `400` invalid parameters or body limit, `401` unauthenticated, `403` missing scope or persona/scope violation, `404` not found or feature disabled, `429` rate limited (with a `Retry-After` header), `500` server error.

## curl examples

```bash
curl -s http://127.0.0.1:7801/page/external/recall \
  -H "Authorization: Bearer $LIVINGMEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"project meeting","k":5}'
```

```bash
curl -s http://127.0.0.1:7801/page/external/memories \
  -H "Authorization: Bearer $LIVINGMEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content":"External agent noted the 2.10.1 release on Friday","importance":0.7,"idempotency_key":"release-2.10.1"}'
```

## MCP configuration

```json
{
  "mcpServers": {
    "livingmemory": {
      "command": "python",
      "args": ["scripts/memory_mcp_server.py"],
      "env": {
        "LIVINGMEMORY_API_BASE": "http://127.0.0.1:7801/page/external",
        "LIVINGMEMORY_API_KEY": "lm_xxx"
      }
    }
  }
}
```

- The bridge only uses the Python standard library; `stdout` carries JSON-RPC only and logs go to `stderr`.
- It exposes `search_memory`, `save_memory` and `list_personas`; API failures are returned as MCP tool errors.

## Security recommendations

- Keep the feature off by default and disable it again when no client needs it.
- Keep the listen host at `127.0.0.1`; put it behind an HTTPS reverse proxy for remote access.
- Create least-privilege keys: read-only keys omit `write`, and persona/scope allowlists limit visible data.
- Rotate keys regularly: create the new key, update clients, then revoke the old key.
- The audit log lives in the plugin data directory as `external_api_audit.jsonl` and never stores memory content or plaintext tokens.

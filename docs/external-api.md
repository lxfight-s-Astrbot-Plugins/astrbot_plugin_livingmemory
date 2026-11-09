# 外部记忆 API

外部记忆 API 让 Codex、WorkBuddy、Qoder 等 Agent 终端通过 HTTP 读写 LivingMemory 中的长期记忆。功能默认关闭；开启后插件会启动一个独立的本机 HTTP 监听，并随插件附带零依赖的 MCP bridge。

> 外部 API 复用与聊天写入相同的 `MemoryEngine` 路径：去重、Embedding、图记忆和记忆原子都会照常生效。API Key 只保存哈希，明文令牌仅在创建时显示一次。

## 启用

1. 在 AstrBot 插件配置的「外部记忆 API」分组中开启 `external_api.enabled`，按需调整监听地址与端口。
2. 重载插件，确认日志出现外部记忆 API 已监听。
3. 使用聊天命令或 Dashboard 生成 API Key（令牌只返回一次）。

```bash
# 默认仅监听本机，可通过反向代理对外暴露
127.0.0.1:7801
```

| 配置项 | 说明 |
| --- | --- |
| `external_api.enabled` | 总开关，默认关闭；关闭时所有外部接口返回 404 |
| `external_api.listen_host` | 监听地址，默认 `127.0.0.1` |
| `external_api.listen_port` | 监听端口，默认 `7801` |
| `external_api.max_request_bytes` | 请求体上限，默认 256 KiB |
| `external_api.default_rate_limit_per_min` | 新建 Key 的默认每分钟请求数 |
| `external_api.default_write_limit_per_min` | 新建 Key 的默认每分钟写入数 |
| `external_api.audit_retention_days` | 审计日志保留天数 |
| `external_api.trust_proxy_headers` | 是否信任 `X-Forwarded-For` 作为审计客户端地址 |

## 生成 API Key

```text
/lmem apikey create workbuddy write
/lmem apikey create codex read p1,p2 livingmemory:global 120
/lmem apikey list
/lmem apikey revoke workbuddy
```

- 第一个参数是唯一名称，第二个参数为 `read` 或 `write`（写权限同时包含读权限）。
- 第三、四个参数可选：允许的人格列表与作用域列表，逗号分隔。
- 第五个参数可选：该 Key 的每分钟请求上限。
- 也可以调用需要宿主登录的 Page API：`GET /page/external/keys`、`POST /page/external/keys`、`DELETE /page/external/keys/<key_id>`。

## 接口一览

所有请求使用 `Authorization: Bearer <令牌>`；成功响应的 `status` 为 `ok`，失败响应为 `{status:"error", code, message}`。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /page/external/health` | 插件版本与就绪状态，需要读权限 |
| `GET /page/external/personas` | 可用人格与作用域列表，需要读权限 |
| `POST /page/external/recall` | 召回记忆，请求 `{query, k?, persona_id?, scope?}` |
| `POST /page/external/memories` | 写入记忆，支持 `idempotency_key` |
| `GET /page/external/memories/<memory_id>` | 读取单条记忆，需要读权限 |
| `POST /page/external/memories/<memory_id>/archive` | 软归档单条记忆，需要写权限 |

状态码：`400` 参数或请求体越界、`401` 未授权、`403` 权限或人格/作用域越界、`404` 未找到或功能未启用、`429` 限流（响应头带 `Retry-After`）、`500` 服务端错误。

## curl 示例

```bash
curl -s http://127.0.0.1:7801/page/external/recall \
  -H "Authorization: Bearer $LIVINGMEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"项目会议","k":5}'
```

```bash
curl -s http://127.0.0.1:7801/page/external/memories \
  -H "Authorization: Bearer $LIVINGMEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content":"外部 Agent 记录：周五发布 2.10.1","importance":0.7,"idempotency_key":"release-2.10.1"}'
```

## MCP 配置

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

- bridge 仅依赖 Python 标准库，`stdout` 只输出 JSON-RPC，日志写入 `stderr`。
- 提供 `search_memory`、`save_memory`、`list_personas` 三个工具，错误以工具错误返回。

## 安全建议

- 保持默认关闭；只在需要时开启，并在不用时关闭。
- 监听地址保持 `127.0.0.1`；需要远程访问时置于 HTTPS 反向代理之后。
- 按最小权限创建 Key：只读 Key 不开启 `write`，并通过人格/作用域限制可见数据。
- 定期轮换 Key：先创建新 Key，替换客户端配置后再吊销旧 Key。
- 审计日志位于插件数据目录 `external_api_audit.jsonl`，不记录记忆内容与明文令牌。

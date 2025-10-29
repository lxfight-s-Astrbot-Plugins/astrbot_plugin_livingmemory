# LivingMemory WebUI API 文档

## 概述

LivingMemory WebUI基于新的MemoryEngine架构重构,提供RESTful API用于记忆管理和系统监控。

**版本**: 2.0.0  
**基础URL**: `http://localhost:8080`  
**认证方式**: Bearer Token

---

## 认证

### POST /api/login
用户登录获取访问Token

**请求体**:
```json
{
  "password": "your_password"
}
```

**响应**:
```json
{
  "token": "random_token_string",
  "expires_in": 3600
}
```

**错误码**:
- `400`: 密码不能为空
- `401`: 认证失败
- `429`: 尝试次数过多

---

### POST /api/logout
用户登出,使Token失效

**Headers**: `Authorization: Bearer <token>`

**响应**:
```json
{
  "detail": "已退出登录"
}
```

---

### GET /api/health
健康检查(无需认证)

**响应**:
```json
{
  "status": "ok",
  "version": "2.0.0"
}
```

---

## 记忆管理

### GET /api/memories
获取记忆列表

**Headers**: `Authorization: Bearer <token>`

**查询参数**:
- `session_id` (可选): 会话ID筛选
- `limit` (可选): 返回数量限制,默认50,最大200

**响应**:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": 1,
        "text": "记忆内容",
        "metadata": {
          "session_id": "session_123",
          "persona_id": "persona_1",
          "importance": 0.8,
          "create_time": 1234567890.0,
          "last_access_time": 1234567890.0
        }
      }
    ],
    "total": 100,
    "limit": 50
  }
}
```

---

### GET /api/memories/{memory_id}
获取单个记忆详情

**Headers**: `Authorization: Bearer <token>`

**路径参数**:
- `memory_id`: 记忆ID (整数)

**响应**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "text": "记忆内容",
    "metadata": {
      "session_id": "session_123",
      "importance": 0.8,
      "create_time": 1234567890.0
    }
  }
}
```

**错误码**:
- `404`: 记忆不存在

---

### POST /api/memories/search
搜索记忆

**Headers**: `Authorization: Bearer <token>`

**请求体**:
```json
{
  "query": "搜索关键词",
  "k": 10,
  "session_id": "session_123",
  "persona_id": "persona_1"
}
```

**参数说明**:
- `query` (必需): 搜索查询字符串
- `k` (可选): 返回结果数量,默认10,最大50
- `session_id` (可选): 会话ID过滤
- `persona_id` (可选): 人格ID过滤

**响应**:
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "content": "记忆内容",
      "score": 0.95,
      "metadata": {
        "session_id": "session_123",
        "importance": 0.8
      }
    }
  ]
}
```

---

### DELETE /api/memories/{memory_id}
删除单个记忆

**Headers**: `Authorization: Bearer <token>`

**路径参数**:
- `memory_id`: 记忆ID (整数)

**响应**:
```json
{
  "success": true,
  "message": "记忆 1 已删除"
}
```

**错误码**:
- `404`: 记忆不存在

---

### POST /api/memories/batch-delete
批量删除记忆

**Headers**: `Authorization: Bearer <token>`

**请求体**:
```json
{
  "memory_ids": [1, 2, 3, 4, 5]
}
```

**响应**:
```json
{
  "success": true,
  "data": {
    "deleted_count": 4,
    "failed_count": 1,
    "total": 5
  }
}
```

---

## 系统管理

### GET /api/stats
获取统计信息

**Headers**: `Authorization: Bearer <token>`

**响应**:
```json
{
  "success": true,
  "data": {
    "total_memories": 150,
    "sessions": {
      "session_123": 50,
      "session_456": 100
    },
    "avg_importance": 0.65,
    "oldest_memory": 1234567890.0,
    "newest_memory": 1234567999.0
  }
}
```

---

### POST /api/cleanup
清理旧记忆

**Headers**: `Authorization: Bearer <token>`

**请求体** (可选):
```json
{
  "days_threshold": 30,
  "importance_threshold": 0.3
}
```

**参数说明**:
- `days_threshold` (可选): 天数阈值,默认使用配置值
- `importance_threshold` (可选): 重要性阈值,默认使用配置值

**响应**:
```json
{
  "success": true,
  "data": {
    "deleted_count": 25,
    "message": "已清理 25 条旧记忆"
  }
}
```

---

### GET /api/sessions
获取会话列表

**Headers**: `Authorization: Bearer <token>`

**响应**:
```json
{
  "success": true,
  "data": {
    "sessions": [
      {
        "session_id": "session_123",
        "memory_count": 100
      },
      {
        "session_id": "session_456",
        "memory_count": 50
      }
    ],
    "total": 2
  }
}
```

**说明**: 结果按记忆数量降序排列

---

### GET /api/config
获取配置信息

**Headers**: `Authorization: Bearer <token>`

**响应**:
```json
{
  "success": true,
  "data": {
    "session_timeout": 3600,
    "memory_config": {
      "rrf_k": 60,
      "decay_rate": 0.01,
      "importance_weight": 1.0,
      "cleanup_days_threshold": 30,
      "cleanup_importance_threshold": 0.3
    }
  }
}
```

**说明**: 仅返回非敏感配置信息

---

## 错误处理

所有API在发生错误时返回统一格式:

```json
{
  "success": false,
  "error": "错误描述信息"
}
```

**常见HTTP状态码**:
- `200`: 成功
- `400`: 请求参数错误
- `401`: 未认证或Token无效
- `404`: 资源不存在
- `429`: 请求过于频繁
- `500`: 服务器内部错误

---

## 安全特性

1. **密码认证**: 所有API(除login和health)需要Token认证
2. **Token管理**: 
   - 24小时绝对过期
   - 可配置的活动超时(默认1小时)
3. **请求频率限制**: 5分钟内最多5次登录失败尝试
4. **CORS配置**: 限制跨域访问来源

---

## 使用示例

### Python示例

```python
import requests

# 登录
response = requests.post('http://localhost:8080/api/login', 
                         json={'password': 'your_password'})
token = response.json()['token']

headers = {'Authorization': f'Bearer {token}'}

# 获取统计信息
stats = requests.get('http://localhost:8080/api/stats', headers=headers)
print(stats.json())

# 搜索记忆
search_result = requests.post('http://localhost:8080/api/memories/search',
                              headers=headers,
                              json={'query': '关键词', 'k': 5})
print(search_result.json())

# 登出
requests.post('http://localhost:8080/api/logout', headers=headers)
```

### JavaScript示例

```javascript
// 登录
const loginResponse = await fetch('http://localhost:8080/api/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ password: 'your_password' })
});
const { token } = await loginResponse.json();

// 获取记忆列表
const memoriesResponse = await fetch('http://localhost:8080/api/memories?limit=20', {
  headers: { 'Authorization': `Bearer ${token}` }
});
const memories = await memoriesResponse.json();
console.log(memories);

// 搜索记忆
const searchResponse = await fetch('http://localhost:8080/api/memories/search', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({ query: '关键词', k: 10 })
});
const searchResults = await searchResponse.json();
console.log(searchResults);
```

---

## 更新日志

### v2.0.0 (当前版本)
-  完全重构基于MemoryEngine架构
-  移除对旧引擎和Handler的依赖
-  简化API端点,专注核心功能
-  改进错误处理和响应格式
-  增强安全性(Token管理、频率限制)
-  统一使用JSON响应格式
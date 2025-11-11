# 机器人记忆隔离功能升级说明

## 更新日期
2025年11月11日

## 问题背景

在原有实现中，插件声称支持"按人格隔离记忆"，但实际上只能按 `unified_msg_origin` (格式：`平台名:消息类型:会话ID`) 来区分记忆。

**关键问题**：当同一个用户账号与部署在同一平台上的多个不同机器人对话时：
- 机器人A: `unified_msg_origin = "aiocqhttp:private:用户QQ号"`
- 机器人B: `unified_msg_origin = "aiocqhttp:private:用户QQ号"`

两者完全相同，导致记忆无法隔离！

## 解决方案

### 核心思路
引入**复合会话ID**机制，同时使用：
1. `event.get_self_id()` - 机器人自身ID（区分不同机器人）
2. `unified_msg_origin` - 原有的会话标识（区分平台/用户/群组）

### 复合会话ID格式
```
bot_{机器人ID}:{原始会话ID}
```

**示例**：
- 机器人A（ID: 111111）: `"bot_111111:aiocqhttp:private:333333"`
- 机器人B（ID: 222222）: `"bot_222222:aiocqhttp:private:333333"`

## 主要修改

### 1. 配置文件更新 (`_conf_schema.json`)

新增配置项：
```json
{
  "filtering_settings": {
    "use_bot_isolation": {
      "description": "启用机器人记忆隔离",
      "type": "bool",
      "default": true
    }
  }
}
```

### 2. 核心函数新增 (`core/utils/__init__.py`)

新增函数 `get_bot_session_id()`:
```python
def get_bot_session_id(event: AstrMessageEvent, use_bot_isolation: bool = True) -> str:
    """
    生成复合会话ID
    
    Args:
        event: AstrBot消息事件
        use_bot_isolation: 是否启用机器人隔离
    
    Returns:
        - 启用: "bot_{self_id}:{unified_msg_origin}"
        - 不启用: "{unified_msg_origin}"
    """
```

**特性**：
- ✅ 支持开关控制（向后兼容）
- ✅ 自动降级（无法获取self_id时回退到原始行为）
- ✅ 详细的日志记录

### 3. UUID提取函数修复 (`core/memory_engine.py`) ⚠️ **关键修复**

**问题**：原有的 `_extract_session_uuid()` 函数会将复合会话ID拆解，导致隔离失效。

**修复前**：
```python
# 输入: "bot_111111:aiocqhttp:private:333333"
# 输出: "333333"  ❌ 只保留最后一部分，机器人标识丢失！
```

**修复后**：
```python
def _extract_session_uuid(session_id: str | None) -> str | None:
    # 如果是机器人隔离格式(bot_开头)，直接返回完整ID
    if session_id.startswith("bot_"):
        return session_id
    
    # 其他格式照旧提取UUID
    if ":" in session_id:
        parts = session_id.split(":")
        return parts[-1]
    ...
```

**修复后**：
```python
# 输入: "bot_111111:aiocqhttp:private:333333"
# 输出: "bot_111111:aiocqhttp:private:333333"  ✅ 保持完整，隔离生效！
```

### 4. 记忆召回逻辑更新 (`main.py::handle_memory_recall`)

**修改前**：
```python
session_id = event.session_id
```

**修改后**：
```python
filtering_config = self.config.get("filtering_settings", {})
use_bot_isolation = filtering_config.get("use_bot_isolation", True)
session_id = get_bot_session_id(event, use_bot_isolation)
```

### 4. 记忆存储逻辑更新 (`main.py::handle_memory_reflection`)

同样使用复合会话ID生成函数，确保召回和存储使用相同的会话标识。

### 5. 会话管理器更新 (`core/conversation_manager.py`)

修改 `add_message_from_event()` 方法，支持自定义会话ID：
```python
async def add_message_from_event(
    self,
    event: Any,
    role: str,
    content: str,
    custom_session_id: str | None = None,  # 新增参数
) -> Message:
```

### 6. 配置验证器更新 (`core/config_validator.py`)

在 `FilteringConfig` 类中新增字段：
```python
use_bot_isolation: bool = Field(
    default=True, 
    description="是否启用机器人记忆隔离"
)
```

## 向后兼容性

### ✅ 完全兼容
1. **配置兼容**：`use_bot_isolation` 默认为 `true`，但可以关闭
2. **数据兼容**：旧数据仍然可以正常访问（会话ID格式不同但不冲突）
3. **功能降级**：如果无法获取 `self_id`，自动回退到原始行为

### 使用建议

#### 场景1：多机器人部署（推荐启用）
```json
{
  "filtering_settings": {
    "use_bot_isolation": true,
    "use_persona_filtering": true,
    "use_session_filtering": true
  }
}
```

**效果**：
- ✅ 不同机器人的记忆完全隔离
- ✅ 同一机器人的不同人格记忆隔离
- ✅ 不同会话的记忆隔离

#### 场景2：单机器人部署（可关闭）
```json
{
  "filtering_settings": {
    "use_bot_isolation": false,
    "use_persona_filtering": true,
    "use_session_filtering": true
  }
}
```

**效果**：
- 保持原有行为
- 减少会话ID复杂度

## 测试验证

### 测试场景1：多机器人记忆隔离
1. 部署机器人A（QQ: 111111）和机器人B（QQ: 222222）
2. 用户（QQ: 333333）分别与两个机器人对话
3. 验证：
   - 对A说的话不会被B召回 ✅
   - 对B说的话不会被A召回 ✅

### 测试场景2：配置开关测试
1. 设置 `use_bot_isolation: false`
2. 验证降级到原有行为 ✅

### 测试场景3：降级兼容性
1. 在无法获取 `self_id` 的平台上测试
2. 验证自动降级并记录警告日志 ✅

## 日志输出示例

### 启用机器人隔离
```
[DEBUG-Recall] 生成会话ID: bot_111111:aiocqhttp:private:333333 (bot_isolation=True)
[aiocqhttp:private:333333] 过滤参数: session_id=bot_111111:aiocqhttp:private:333333, persona_id=assistant, use_session=True, use_persona=True, use_bot_isolation=True
```

### 关闭机器人隔离
```
[DEBUG-Recall] 生成会话ID: aiocqhttp:private:333333 (bot_isolation=False)
[aiocqhttp:private:333333] 过滤参数: session_id=aiocqhttp:private:333333, persona_id=assistant, use_session=True, use_persona=True, use_bot_isolation=False
```

### 降级场景
```
[WARNING] 无法获取机器人ID (self_id)，已降级为不隔离模式。unified_msg_origin=aiocqhttp:private:333333
```

## 文件修改清单

1. ✅ `_conf_schema.json` - 添加配置项
2. ✅ `core/utils/__init__.py` - 新增复合会话ID生成函数
3. ✅ `core/config_validator.py` - 更新配置验证器
4. ✅ `main.py` - 更新记忆召回和存储逻辑
5. ✅ `core/conversation_manager.py` - 支持自定义会话ID

## 注意事项

1. **首次启用后的行为**：
   - 新对话会使用新的复合会话ID
   - 旧记忆仍然使用旧的会话ID存储
   - 两者不会冲突，但也不会互相召回

2. **迁移建议**：
   - 如果需要迁移旧记忆到新格式，请手动执行数据迁移
   - 或者清空旧记忆重新开始

3. **性能影响**：
   - 会话ID变长，但对性能影响微乎其微
   - 索引和检索逻辑无需修改

## 升级步骤

1. 备份现有数据（可选）
2. 拉取最新代码
3. 检查配置文件，确认 `use_bot_isolation` 设置
4. 重启插件
5. 测试多机器人场景

## 常见问题

### Q: 升级后旧记忆还能用吗？
A: 可以！旧记忆使用旧的会话ID存储，新记忆使用新的复合会话ID。它们各自独立，不会互相干扰。

### Q: 可以关闭这个功能吗？
A: 可以！设置 `use_bot_isolation: false` 即可恢复到原有行为。

### Q: 单机器人场景需要启用吗？
A: 不是必须的。但启用也没有坏处，只是会话ID会带上机器人标识前缀。

### Q: 如何验证功能是否生效？
A: 查看日志中的会话ID格式：
- 启用：`bot_{机器人ID}:...`
- 未启用：直接是原始格式

---

**作者**：GitHub Copilot  
**协作者**：thinkthink191  
**版本**：v1.6.3+

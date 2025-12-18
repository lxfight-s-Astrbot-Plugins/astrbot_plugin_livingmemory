# LivingMemory 开发者指南

**版本**: v2.0.0
**更新日期**: 2025-12-17

---

## 目录

1. [开发环境设置](#开发环境设置)
2. [项目结构](#项目结构)
3. [开发工作流](#开发工作流)
4. [测试指南](#测试指南)
5. [代码规范](#代码规范)
6. [调试技巧](#调试技巧)
7. [贡献指南](#贡献指南)

---

## 开发环境设置

### 前置要求

- Python 3.10+
- AstrBot 开发环境
- Git

### 安装依赖

```bash
# 克隆仓库
git clone https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory.git
cd astrbot_plugin_livingmemory

# 安装依赖
pip install -r requirements.txt

# 安装开发依赖
pip install pytest pytest-asyncio pytest-cov
```

### IDE配置

推荐使用 VSCode 或 PyCharm。

**VSCode 配置** (`.vscode/settings.json`):
```json
{
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": true,
  "python.formatting.provider": "black",
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false
}
```

---

## 项目结构

```
astrbot_plugin_livingmemory/
├── main.py                          # 插件主文件
├── core/                            # 核心模块
│   ├── exceptions.py                # 异常定义
│   ├── config_manager.py            # 配置管理
│   ├── plugin_initializer.py       # 插件初始化
│   ├── event_handler.py            # 事件处理
│   ├── command_handler.py          # 命令处理
│   ├── memory_engine.py            # 记忆引擎
│   ├── memory_processor.py         # 记忆处理
│   ├── conversation_manager.py     # 会话管理
│   └── ...
├── storage/                        # 存储层
│   ├── conversation_store.py       # 会话存储
│   └── db_migration.py             # 数据库迁移
├── webui/                          # Web管理界面
│   └── server.py                   # FastAPI服务器
├── tests/                          # 测试套件
│   ├── conftest.py                 # pytest配置
│   ├── test_*.py                   # 单元测试
│   ├── integration/                # 集成测试
│   └── performance_test.py         # 性能测试
├── docs/                           # 文档
│   ├── API.md                      # API文档
│   └── DEVELOPMENT.md              # 开发者指南
```

### 模块职责

| 模块 | 职责 | 依赖 |
|------|------|------|
| main.py | 插件注册和生命周期 | 所有核心模块 |
| exceptions.py | 异常定义 | 无 |
| config_manager.py | 配置管理 | config_validator |
| plugin_initializer.py | 插件初始化 | 所有核心模块 |
| event_handler.py | 事件处理 | memory_engine, conversation_manager |
| command_handler.py | 命令处理 | memory_engine, conversation_manager |

---

## 开发工作流

### 1. 创建新功能

```bash
# 创建新分支
git checkout -b feature/your-feature-name

# 开发功能
# ...

# 运行测试
pytest tests/

# 提交代码
git add .
git commit -m "feat: add your feature"
git push origin feature/your-feature-name
```

### 2. 修复Bug

```bash
# 创建修复分支
git checkout -b fix/bug-description

# 修复bug
# ...

# 添加测试
# ...

# 运行测试
pytest tests/

# 提交代码
git add .
git commit -m "fix: fix bug description"
git push origin fix/bug-description
```

### 3. 重构代码

```bash
# 创建重构分支
git checkout -b refactor/what-to-refactor

# 重构代码
# ...

# 确保所有测试通过
pytest tests/

# 提交代码
git add .
git commit -m "refactor: refactor description"
git push origin refactor/what-to-refactor
```

---

## 测试指南

### 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试文件
pytest tests/test_config_manager.py

# 运行特定测试函数
pytest tests/test_config_manager.py::test_config_manager_initialization

# 查看覆盖率
pytest --cov=core tests/

# 生成HTML覆盖率报告
pytest --cov=core --cov-report=html tests/
```

### 编写测试

#### 单元测试示例

```python
import pytest
from core.config_manager import ConfigManager

def test_config_manager_get():
    """测试配置获取"""
    config = ConfigManager({"key": "value"})
    assert config.get("key") == "value"
    assert config.get("non_existent", "default") == "default"

@pytest.mark.asyncio
async def test_async_function():
    """测试异步函数"""
    result = await some_async_function()
    assert result is not None
```

#### Mock使用示例

```python
from unittest.mock import Mock, AsyncMock, patch

def test_with_mock():
    """使用mock测试"""
    mock_obj = Mock()
    mock_obj.method = Mock(return_value="test")

    result = mock_obj.method()
    assert result == "test"
    assert mock_obj.method.called

@pytest.mark.asyncio
async def test_with_async_mock():
    """使用async mock测试"""
    mock_obj = Mock()
    mock_obj.async_method = AsyncMock(return_value="test")

    result = await mock_obj.async_method()
    assert result == "test"
```

### 性能测试

```bash
# 运行性能测试
python3 tests/performance_test.py
```

---

## 代码规范

### Python代码风格

遵循 PEP 8 规范：

```python
# 好的示例
def calculate_score(
    importance: float,
    recency: float,
    weight: float = 1.0
) -> float:
    """
    计算最终分数

    Args:
        importance: 重要性
        recency: 时效性
        weight: 权重

    Returns:
        最终分数
    """
    return importance * recency * weight


# 避免的示例
def calc(i,r,w=1.0):  # 命名不清晰，缺少类型注解
    return i*r*w  # 缺少文档字符串
```

### 命名规范

- **类名**: PascalCase (如 `ConfigManager`)
- **函数名**: snake_case (如 `get_config`)
- **常量**: UPPER_SNAKE_CASE (如 `MAX_RETRIES`)
- **私有方法**: 前缀下划线 (如 `_internal_method`)

### 文档字符串

使用 Google 风格的文档字符串：

```python
def process_memory(
    content: str,
    metadata: dict,
    importance: float = 0.5
) -> tuple[str, dict, float]:
    """
    处理记忆内容

    Args:
        content: 记忆内容
        metadata: 元数据字典
        importance: 重要性评分

    Returns:
        tuple: (处理后的内容, 更新的元数据, 最终重要性)

    Raises:
        MemoryProcessingError: 处理失败时抛出

    Example:
        >>> content, meta, score = process_memory("test", {}, 0.8)
        >>> print(score)
        0.8
    """
    # 实现...
    pass
```

### 类型注解

所有公开函数都应该有完整的类型注解：

```python
from typing import Any, Optional, List, Dict

def get_memories(
    session_id: str,
    limit: int = 10,
    filters: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """获取记忆列表"""
    pass
```

---

## 调试技巧

### 使用日志

```python
from astrbot.api import logger

# 不同级别的日志
logger.debug("调试信息")
logger.info("一般信息")
logger.warning("警告信息")
logger.error("错误信息", exc_info=True)  # 包含堆栈跟踪
```

### 使用断点

```python
# 在代码中添加断点
import pdb; pdb.set_trace()

# 或使用 breakpoint() (Python 3.7+)
breakpoint()
```

### 查看变量

```python
# 打印变量
print(f"变量值: {variable}")

# 使用 logger
logger.debug(f"变量值: {variable}")

# 使用 pprint 格式化输出
from pprint import pprint
pprint(complex_dict)
```

### 性能分析

```python
import time

start_time = time.time()
# 执行操作
end_time = time.time()
logger.info(f"操作耗时: {end_time - start_time:.4f}秒")
```

---

## 贡献指南

### 提交规范

使用 Conventional Commits 规范：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**类型**:
- `feat`: 新功能
- `fix`: Bug修复
- `docs`: 文档更新
- `style`: 代码格式（不影响功能）
- `refactor`: 重构
- `test`: 测试相关
- `chore`: 构建/工具相关

**示例**:
```
feat(memory): add memory importance decay

实现了基于时间的记忆重要性衰减机制，使用指数衰减函数。

Closes #123
```

### Pull Request流程

1. Fork 仓库
2. 创建功能分支
3. 编写代码和测试
4. 确保所有测试通过
5. 提交 Pull Request
6. 等待代码审查
7. 根据反馈修改
8. 合并到主分支

### 代码审查清单

- [ ] 代码符合规范
- [ ] 有完整的类型注解
- [ ] 有文档字符串
- [ ] 有单元测试
- [ ] 所有测试通过
- [ ] 没有引入新的依赖（或已说明）
- [ ] 更新了相关文档
- [ ] 提交信息清晰

---

## 常见问题

### Q: 如何添加新的配置项？

A:
1. 在 `_conf_schema.json` 中添加配置定义
2. 在 `config_validator.py` 中添加验证逻辑
3. 在 `ConfigManager` 中添加访问方法（如需要）
4. 更新文档

### Q: 如何添加新的命令？

A:
1. 在 `CommandHandler` 中添加处理方法
2. 在 `main.py` 中添加命令装饰器
3. 添加单元测试
4. 更新帮助文档

### Q: 如何调试初始化问题？

A:
1. 检查日志输出
2. 使用 `initialization_status_callback` 获取状态
3. 检查 Provider 是否就绪
4. 查看 `_initialization_error` 属性

---

## 资源链接

- [API文档](API.md)
- [架构文档](ARCHITECTURE.md)
- [GitHub仓库](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)
- [问题反馈](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues)

---

**文档版本**: v2.0.0
**最后更新**: 2025-12-17

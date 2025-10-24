# LivingMemory - 动态生命周期记忆插件 v1.3.3


<div align="center">

<img src="https://img.shields.io/badge/状态-测...测不动了-warning?style=for-the-badge&logo=github" alt="测...测不动了" />

<details>
<summary><strong>🧪 测...测不动了</strong></summary>

> ⚠️ **需要测试验证**: 请在测试环境中验证所有新功能

</details>

</div>


<p align="center">
  <i>为 AstrBot 打造的、拥有完整记忆生命周期的智能长期记忆插件。</i>
  <br><br>
  <!-- 技术徽章 -->
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/Faiss-CPU-orange.svg" alt="Faiss">
  <img src="https://img.shields.io/github/license/lxfight/astrbot_plugin_livingmemory?style=flat-square&color=green" alt="License">
  <!-- GitHub 统计 -->
  <a href="https://github.com/lxfight/astrbot_plugin_livingmemory">
    <img src="https://img.shields.io/github/stars/lxfight/astrbot_plugin_livingmemory?style=social" alt="GitHub Stars">
  </a>
  <!-- 访客计数器 -->
  <img src="https://komarev.com/ghpvc/?username=lxfight&repo=astrbot_plugin_livingmemory&color=blueviolet" alt="Visitor Count">
</p>


---

`LivingMemory` 告别了传统记忆插件对大型数据库的依赖，创新性地采用轻量级的 `Faiss` 和 `SQLite` 作为存储后端。这不仅实现了 **零配置部署** 和 **极低的资源消耗**，更引入了革命性的 **动态记忆生命周期 (Dynamic Memory Lifecycle)** 模型。

## ✨ 核心特性：三大引擎架构

本插件通过三大智能引擎的协同工作，完美模拟了人类记忆的形成、巩固、联想和遗忘的全过程。

| 引擎 | 图标 | 核心功能 | 最新增强 |
| :--- | :---: | :--- | :--- |
| **反思引擎** | 🧠 | `智能总结` & `重要性评估` | ✨ 增强错误重试机制，提高提取成功率 |
| **回忆引擎** | 🔍 | `混合检索` & `智能融合` | 🚀 **9种先进融合策略** - RRF、自适应、级联等 |
| **遗忘代理** | 🗑️ | `遗忘曲线` & `批量清理` | 💾 **分页处理** - 优化内存使用，支持大规模数据 |

## 🔥 新特性：混合检索系统

### 检索模式
- **🔍 Dense (密集检索)**: 基于语义向量的深度理解
- **⚡ Sparse (稀疏检索)**: BM25关键词匹配，支持中文分词
- **🤝 Hybrid (混合检索)**: 智能融合两种检索方式的优势

### 9种融合策略

<details>
<summary><strong>📊 查看所有融合策略详情（点击展开）</strong></summary>

| 策略 | 特点 | 适用场景 | 计算复杂度 |
|------|------|----------|------------|
| **RRF** | 经典倒数排名融合 | 通用场景，平衡性好 | 低 |
| **Hybrid RRF** | 动态参数调整 | 自适应查询类型 | 中 |
| **Weighted** | 简单加权融合 | 明确权重偏好 | 低 |
| **Convex** | 凸组合数学融合 | 需要严格数学性质 | 低 |
| **Interleave** | 交替选择结果 | 保证结果多样性 | 低 |
| **Rank Fusion** | 基于排序位置 | 重视排序信息 | 中 |
| **Score Fusion** | Borda Count投票 | 民主投票机制 | 高 |
| **Cascade** | 两阶段处理 | 大规模高效检索 | 低 |
| **Adaptive** | 查询特征自适应 | 多样化查询场景 | 中 |

</details>

### 智能查询分析
系统会自动分析查询特征，选择最优融合策略：
- **关键词查询** → 偏向稀疏检索
- **语义查询** → 偏向密集检索  
- **混合查询** → 使用RRF平衡融合

## 🚀 快速开始

### 1. 安装

将 `astrbot_plugin_livingmemory` 文件夹放置于 AstrBot 的 `data/plugins` 目录下。AstrBot 将自动检测并安装依赖。

**核心依赖:**
```
faiss-cpu>=1.7.0
pydantic>=1.8.0
jieba>=0.42.1
```

**🧪 测试阶段**: 插件已完成重构和功能增强，正在进行全面测试验证。

### 2. 配置

**✨ 全新配置系统**: 基于 Pydantic 的智能配置验证，确保参数有效性。

<details>
<summary><strong>⚙️ 点击展开详细配置说明</strong></summary>

#### 🔧 基础设置
- **Provider设置**: 自定义 Embedding 和 LLM Provider，支持多Provider混用
- **时区配置**: 支持全球时区，时间显示本地化
- **会话管理**: 智能会话生命周期，自动清理过期会话

#### 🔍 检索配置  
- **检索模式**: `hybrid`(混合) | `dense`(密集) | `sparse`(稀疏)
- **融合策略**: 9种策略可选，支持动态参数调整
- **BM25参数**: 可调整k1、b参数优化中文检索效果
- **权重控制**: 相似度、重要性、新近度权重精细调节

#### 🧠 智能引擎配置
- **反思触发**: 可配置对话轮次阈值(1-100轮)  
- **重要性评估**: 自定义重要性阈值(0.0-1.0)
- **自定义提示词**: 完全可定制的事件提取和评估提示

#### 🗑️ 遗忘机制
- **智能清理**: 基于重要性衰减的自动清理
- **批量处理**: 分页加载，支持大规模记忆库
- **保留策略**: 灵活的天数和阈值配置

#### 🛡️ 过滤隔离
- **人格过滤**: 按AI人格隔离记忆，互不干扰
- **会话隔离**: 会话级别的记忆独立性
- **状态管理**: 记忆状态(活跃/归档/删除)精细控制

</details>

#### 🔐 WebUI 控制台

- **访问安全**：新增 `webui_settings.access_password` 入口密码校验，未通过验证无法进入面板。
- **端口可配**：默认监听 `8080`，通过 `webui_settings.port` 自定义端口，支持只监听本地或外部地址。
- **可视化功能**：登陆后即可查看全部记忆、按页浏览、筛选状态、批量勾选删除，并实时查看记忆与会话统计。

```jsonc
{
  "webui_settings": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8080,
    "access_password": "请替换为安全密码",
    "session_timeout": 3600
  }
}
```

> 启用 WebUI 时务必设置强密码，避免长期记忆泄露。配置生效后访问 `http://host:port` 即可使用白色现代风格的控制台。

- **首次安装或升级插件时**，请先到 AstrBot 插件配置页填写 `webui_settings.access_password`；未设置密码会导致 WebUI 无法启用。
- 如果插件运行在 Docker / 容器环境，为方便外部访问可将 `webui_settings.host` 调整为 `0.0.0.0`，再配合映射的端口进行访问。

### 3. 高级配置示例

```yaml
# 针对中文优化的推荐配置
sparse_retriever:
  bm25_k1: 1.2      # 中文词频参数
  bm25_b: 0.75      # 文档长度归一化
  use_jieba: true   # 启用中文分词

fusion:
  strategy: "hybrid_rrf"    # 自适应融合策略
  rrf_k: 60                # RRF参数
  diversity_bonus: 0.1     # 多样性奖励

recall_engine:
  retrieval_mode: "hybrid"  # 混合检索模式
  similarity_weight: 0.6   # 相似度权重
  importance_weight: 0.2   # 重要性权重  
  recency_weight: 0.2      # 新近度权重
```

## 🛠️ 完整管理命令

<details>
<summary><strong>⚡ 查看所有管理命令（点击展开）</strong></summary>

插件在后台自动运行，同时提供了强大的命令行管理界面：

### 📊 基础管理
| 命令 | 参数 | 描述 |
| :--- | :--- | :--- |
| `/lmem status` | - | 📈 查看记忆库状态和统计信息 |
| `/lmem search` | `<query> [k=3]` | 🔍 手动搜索记忆，支持详细信息展示 |
| `/lmem forget` | `<memory_id>` | 🗑️ 删除指定ID的记忆 |

### 🧠 引擎管理
| 命令 | 参数 | 描述 |
| :--- | :--- | :--- |
| `/lmem run_forgetting_agent` | - | 🔄 手动触发遗忘代理清理任务 |
| `/lmem sparse_rebuild` | - | 🏗️ 重建稀疏检索索引 |
| `/lmem sparse_test` | `<query> [k=5]` | ⚡ 测试稀疏检索功能 |

### ⚙️ 配置管理
| 命令 | 参数 | 描述 |
| :--- | :--- | :--- |
| `/lmem config` | `[show\|validate]` | 📋 显示或验证当前配置 |
| `/lmem search_mode` | `<hybrid\|dense\|sparse>` | 🔄 切换检索模式 |

### 🔄 融合策略管理
| 命令 | 参数 | 描述 |
| :--- | :--- | :--- |
| `/lmem fusion` | `[strategy] [param=value]` | 🎯 管理融合策略和参数 |
| `/lmem test_fusion` | `<query> [k=5]` | 🧪 测试当前融合策略效果 |

### 📝 记忆编辑 *(新增)*
| 命令 | 参数 | 描述 |
| :--- | :--- | :--- |
| `/lmem edit` | `<id> <field> <value> [reason]` | ✏️ 精确编辑记忆内容或元数据 |
| `/lmem update` | `<id>` | 📝 交互式记忆编辑引导 |
| `/lmem history` | `<id>` | 📚 查看记忆的完整更新历史 |

#### 编辑示例
```bash
# 编辑记忆内容
/lmem edit 123 content 这是新的记忆内容 修正错误信息

# 调整重要性评分
/lmem edit 123 importance 0.9 提高重要性

# 更改记忆类型
/lmem edit 123 type PREFERENCE 重新分类为偏好

# 归档记忆
/lmem edit 123 status archived 项目已完成
```

</details>

#### 融合策略示例  
```bash  
# 查看当前融合配置
/lmem fusion show

# 切换到自适应RRF
/lmem fusion hybrid_rrf

# 调整凸组合参数
/lmem fusion convex convex_lambda=0.6

# 调整加权融合权重
/lmem fusion weighted dense_weight=0.8

# 测试融合效果
/lmem test_fusion "用户的兴趣爱好" 5
```

## 🎯 性能优化与最佳实践

### 💾 内存管理
- **分页加载**: 支持大规模记忆库，避免OOM
- **会话清理**: 智能TTL机制，自动清理过期会话
- **事务安全**: SQLite事务保证数据一致性

### 🚀 并发处理
- **异步初始化**: 避免阻塞主线程
- **后台任务**: 反思和遗忘任务后台执行
- **错误重试**: 自动重试机制，提高稳定性

### 🔧 故障排除

<details>
<summary><strong>🚨 常见问题解决方案</strong></summary>

#### Q: 插件初始化失败
```bash
# 检查依赖安装
pip install faiss-cpu pydantic jieba

# 验证配置
/lmem config validate
```

#### Q: 检索效果不佳  
```bash
# 尝试不同融合策略
/lmem fusion adaptive

# 重建稀疏索引
/lmem sparse_rebuild

# 调整检索模式
/lmem search_mode hybrid
```

#### Q: 内存占用过高
```bash
# 手动触发遗忘
/lmem run_forgetting_agent

# 检查会话数量
/lmem config show
```

</details>

## 📚 相关文档

- 📖 [融合策略详解](FUSION_STRATEGIES.md) - 深入了解9种融合算法
- ⚙️ [配置参考](docs/CONFIG.md) - 完整配置参数说明  
- 🔧 [开发指南](docs/DEVELOPMENT.md) - 插件开发和扩展指南

## 🤝 贡献

欢迎各种形式的贡献：

### 📝 提交功能建议

我们已启用 Issue 自动化系统，让功能建议提交更便捷：

1. 访问仓库页面 → 点击 "[Issues](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues)" → "New issue"
2. 选择 "💡 功能建议" 模板
3. 填写表单：
   - **功能描述**: 详细说明要实现的功能
   - **使用场景**: 解释为什么需要这个功能
   - **优先级**: 选择 P0(紧急) 到 P3(可选)
4. 提交后，Issue 将自动添加到项目看板


### 🤝 其他贡献方式

- 🐛 **问题报告**: [GitHub Issues](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues)
- 🔧 **代码贡献**: [Pull Requests](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pulls)
- 📖 **文档改进**: 欢迎改进文档和示例

## 交流一下
遇到问题或想交流使用心得？加入我们的讨论群：
[![加入QQ群](https://img.shields.io/badge/QQ群-953245617-blue?style=flat-square&logo=tencent-qq)](https://qm.qq.com/cgi-bin/qm/qr?k=WdyqoP-AOEXqGAN08lOFfVSguF2EmBeO&jump_from=webapi&authKey=tPyfv90TVYSGVhbAhsAZCcSBotJuTTLf03wnn7/lQZPUkWfoQ/J8e9nkAipkOzwh)

`入关口令`： `lxfight`

## 📄 许可证

本项目遵循 **AGPLv3** 许可证 - 详见 [LICENSE](LICENSE) 文件。

---

<div align="center">
<br>

**⭐ 如果这个项目对您有帮助，请给我们一个 Star！**

<br>

*LivingMemory - 让AI拥有真正的生命记忆 🧠✨*

</div>

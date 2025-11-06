# Changelog

所有重要的更改都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增
- 优化的 GitHub Actions 工作流
  - 增强的自动发布功能，支持从 CHANGELOG.md 和 Git 提交历史自动提取更新描述
  - 新增 Issue 自动分类和标记工作流
  - 智能关键词检测和优先级分类

### 改进
- 完善的 Issue 模板系统
  - Bug 报告模板，包含详细的环境信息收集
  - 功能建议模板
  - 问题咨询模板
  - Issue 模板配置文件

## [1.5.18] - 2025-11-06

### 工作流优化
- 创建了全新的 GitHub Actions 工作流系统
- 自动化版本发布流程
- 智能 Issue 管理

---

注意：请在每次发版前更新此文件，将 [Unreleased] 部分的内容移动到新版本号下。
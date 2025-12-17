#!/usr/bin/env python3
"""
重构验证脚本
检查重构后的代码质量和完整性
"""

import os
import sys
from pathlib import Path


def check_file_exists(filepath: str) -> bool:
    """检查文件是否存在"""
    return Path(filepath).exists()


def count_lines(filepath: str) -> int:
    """统计文件行数"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return len(f.readlines())
    except Exception:
        return 0


def main():
    """主验证流程"""
    print("=" * 60)
    print("LivingMemory v2.0.0 重构验证")
    print("=" * 60)
    print()

    # 1. 检查核心文件
    print("1. 检查核心文件...")
    core_files = {
        "main.py": "插件主文件",
        "core/exceptions.py": "异常定义",
        "core/config_manager.py": "配置管理器",
        "core/plugin_initializer.py": "插件初始化器",
        "core/event_handler.py": "事件处理器",
        "core/command_handler.py": "命令处理器",
    }

    all_exist = True
    for filepath, desc in core_files.items():
        exists = check_file_exists(filepath)
        status = "✅" if exists else "❌"
        lines = count_lines(filepath) if exists else 0
        print(f"  {status} {filepath:40s} ({lines:4d}行) - {desc}")
        if not exists:
            all_exist = False

    print()

    # 2. 检查测试文件
    print("2. 检查测试文件...")
    test_files = {
        "tests/conftest.py": "pytest配置",
        "tests/test_config_manager.py": "ConfigManager测试",
        "tests/test_exceptions.py": "异常模块测试",
    }

    for filepath, desc in test_files.items():
        exists = check_file_exists(filepath)
        status = "✅" if exists else "❌"
        lines = count_lines(filepath) if exists else 0
        print(f"  {status} {filepath:40s} ({lines:4d}行) - {desc}")
        if not exists:
            all_exist = False

    print()

    # 3. 检查文档文件
    print("3. 检查文档文件...")
    doc_files = {
        "REFACTOR_FEATURE_ANALYSIS.md": "功能分析文档",
        "REFACTOR_PLAN.md": "重构计划文档",
        "REFACTOR_SUMMARY.md": "重构总结文档",
        "CHANGELOG.md": "更新日志",
        "metadata.yaml": "插件元数据",
    }

    for filepath, desc in doc_files.items():
        exists = check_file_exists(filepath)
        status = "✅" if exists else "❌"
        lines = count_lines(filepath) if exists else 0
        print(f"  {status} {filepath:40s} ({lines:4d}行) - {desc}")
        if not exists:
            all_exist = False

    print()

    # 4. 统计代码量
    print("4. 代码量统计...")
    main_lines = count_lines("main.py")
    old_main_lines = count_lines("main.py.old")

    new_modules_lines = sum([
        count_lines("core/exceptions.py"),
        count_lines("core/config_manager.py"),
        count_lines("core/plugin_initializer.py"),
        count_lines("core/event_handler.py"),
        count_lines("core/command_handler.py"),
    ])

    total_new_lines = main_lines + new_modules_lines

    print(f"  原始main.py: {old_main_lines}行")
    print(f"  新main.py: {main_lines}行 ({(main_lines/old_main_lines*100):.1f}%)")
    print(f"  新增模块: {new_modules_lines}行")
    print(f"  总计: {total_new_lines}行 ({(total_new_lines/old_main_lines*100):.1f}%)")
    print()

    # 5. 检查语法
    print("5. 检查Python语法...")
    syntax_ok = True
    for filepath in core_files.keys():
        if check_file_exists(filepath):
            result = os.system(f"python3 -m py_compile {filepath} 2>/dev/null")
            status = "✅" if result == 0 else "❌"
            print(f"  {status} {filepath}")
            if result != 0:
                syntax_ok = False

    print()

    # 6. 总结
    print("=" * 60)
    print("验证总结")
    print("=" * 60)

    if all_exist and syntax_ok:
        print("✅ 所有检查通过！")
        print()
        print("重构成果:")
        print(f"  - 代码量优化: {old_main_lines}行 → {total_new_lines}行 ({((total_new_lines-old_main_lines)/old_main_lines*100):.1f}%)")
        print(f"  - main.py简化: {old_main_lines}行 → {main_lines}行 ({((main_lines-old_main_lines)/old_main_lines*100):.1f}%)")
        print(f"  - 新增模块: {len(core_files)-1}个")
        print(f"  - 测试文件: {len(test_files)}个")
        print(f"  - 文档文件: {len(doc_files)}个")
        print()
        print("下一步:")
        print("  1. 运行测试: pytest tests/")
        print("  2. 验证功能: 启动AstrBot测试插件")
        print("  3. 提交代码: git add . && git commit")
        return 0
    else:
        print("❌ 验证失败！")
        if not all_exist:
            print("  - 部分文件缺失")
        if not syntax_ok:
            print("  - 存在语法错误")
        return 1


if __name__ == "__main__":
    sys.exit(main())

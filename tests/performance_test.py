#!/usr/bin/env python3
"""
性能基准测试脚本
测试重构前后的性能对比
"""

import asyncio
import time


class PerformanceTest:
    """性能测试类"""

    def __init__(self):
        self.results: list[tuple[str, float]] = []

    def measure_time(self, name: str):
        """装饰器：测量函数执行时间"""

        def decorator(func):
            async def wrapper(*args, **kwargs):
                start_time = time.time()
                result = await func(*args, **kwargs)
                end_time = time.time()
                duration = end_time - start_time
                self.results.append((name, duration))
                print(f"✓ {name}: {duration:.4f}秒")
                return result

            return wrapper

        return decorator

    async def test_config_loading(self):
        """测试配置加载性能"""
        from core.config_manager import ConfigManager

        @self.measure_time("配置加载")
        async def load_config():
            for _ in range(100):
                config = ConfigManager()
                _ = config.get_all()

        await load_config()

    async def test_exception_creation(self):
        """测试异常创建性能"""
        from core.exceptions import LivingMemoryException

        @self.measure_time("异常创建")
        async def create_exceptions():
            for _ in range(1000):
                exc = LivingMemoryException("test", "TEST_CODE")
                _ = str(exc)

        await create_exceptions()

    async def test_message_dedup(self):
        """测试消息去重性能"""
        from unittest.mock import Mock

        from core.config_manager import ConfigManager
        from core.event_handler import EventHandler

        config_manager = ConfigManager()
        mock_context = Mock()
        mock_memory_engine = Mock()
        mock_memory_processor = Mock()
        mock_conversation_manager = Mock()

        event_handler = EventHandler(
            context=mock_context,
            config_manager=config_manager,
            memory_engine=mock_memory_engine,
            memory_processor=mock_memory_processor,
            conversation_manager=mock_conversation_manager,
        )

        @self.measure_time("消息去重检查")
        async def test_dedup():
            for i in range(1000):
                message_id = f"message_{i}"
                event_handler._mark_message_processed(message_id)
                _ = event_handler._is_duplicate_message(message_id)

        await test_dedup()

    async def test_config_access(self):
        """测试配置访问性能"""
        from core.config_manager import ConfigManager

        config = ConfigManager(
            {
                "section1": {
                    "key1": "value1",
                    "key2": "value2",
                }
            }
        )

        @self.measure_time("配置访问")
        async def access_config():
            for _ in range(10000):
                _ = config.get("section1.key1")
                _ = config.get("section1.key2")
                _ = config.get_section("section1")

        await access_config()

    def print_summary(self):
        """打印性能测试总结"""
        print("\n" + "=" * 60)
        print("性能测试总结")
        print("=" * 60)

        if not self.results:
            print("没有测试结果")
            return

        total_time = sum(duration for _, duration in self.results)

        print(f"\n总测试数: {len(self.results)}")
        print(f"总耗时: {total_time:.4f}秒\n")

        print("详细结果:")
        for name, duration in sorted(self.results, key=lambda x: x[1], reverse=True):
            percentage = (duration / total_time * 100) if total_time > 0 else 0
            print(f"  {name:30s}: {duration:.4f}秒 ({percentage:.1f}%)")

        print("\n性能评估:")
        avg_time = total_time / len(self.results)
        if avg_time < 0.1:
            print("  ✅ 性能优秀")
        elif avg_time < 0.5:
            print("  ✓ 性能良好")
        elif avg_time < 1.0:
            print("  ⚠ 性能一般")
        else:
            print("  ❌ 性能需要优化")


async def main():
    """主测试流程"""
    print("=" * 60)
    print("LivingMemory v2.0.0 性能基准测试")
    print("=" * 60)
    print()

    perf_test = PerformanceTest()

    print("开始性能测试...\n")

    try:
        # 运行各项测试
        await perf_test.test_config_loading()
        await perf_test.test_exception_creation()
        await perf_test.test_message_dedup()
        await perf_test.test_config_access()

        # 打印总结
        perf_test.print_summary()

        print("\n" + "=" * 60)
        print("性能测试完成")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

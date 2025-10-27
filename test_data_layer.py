# -*- coding: utf-8 -*-
"""
简单的测试脚本 - 验证数据模型和存储层的基本功能
仅用于开发阶段的快速验证
"""

import asyncio
import os
import time
from pathlib import Path

from core.conversation_models import Message, Session, MemoryEvent
from storage.conversation_store import ConversationStore


async def test_basic_functionality():
    """测试基本功能"""
    print("=" * 60)
    print("LivingMemory 数据层测试")
    print("=" * 60)

    # 使用临时数据库
    test_db_path = "test_conversation.db"

    # 清理旧的测试数据库
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        print("✓ 清理旧测试数据库")

    # 初始化存储层
    store = ConversationStore(test_db_path)
    await store.initialize()
    print("✓ 数据库初始化成功")

    try:
        # 测试 1: 创建会话
        print("\n[测试 1] 创建会话")
        session = await store.create_session(
            session_id="test_session_001", platform="qq"
        )
        print(f"  - 会话ID: {session.session_id}")
        print(f"  - 平台: {session.platform}")
        print(f"  - 创建时间: {session.created_at}")

        # 测试 2: 添加消息
        print("\n[测试 2] 添加消息")

        # 用户消息 1
        msg1 = Message(
            id=0,
            session_id="test_session_001",
            role="user",
            content="你好,今天天气怎么样?",
            sender_id="user_001",
            sender_name="张三",
            platform="qq",
            timestamp=time.time(),
        )
        msg1_id = await store.add_message(msg1)
        print(f"  - 添加用户消息 (ID: {msg1_id}): {msg1.content[:20]}...")

        # 助手消息
        msg2 = Message(
            id=0,
            session_id="test_session_001",
            role="assistant",
            content="今天天气晴朗,气温适宜,适合外出活动。",
            sender_id="assistant",
            sender_name="AstrBot",
            platform="qq",
            timestamp=time.time(),
        )
        msg2_id = await store.add_message(msg2)
        print(f"  - 添加助手消息 (ID: {msg2_id}): {msg2.content[:20]}...")

        # 用户消息 2 (不同用户 - 模拟群聊)
        msg3 = Message(
            id=0,
            session_id="test_session_001",
            role="user",
            content="我也想知道,准备出去玩!",
            sender_id="user_002",
            sender_name="李四",
            group_id="group_123",
            platform="qq",
            timestamp=time.time(),
        )
        msg3_id = await store.add_message(msg3)
        print(f"  - 添加群聊消息 (ID: {msg3_id}): {msg3.content[:20]}...")

        # 测试 3: 获取会话消息
        print("\n[测试 3] 获取会话消息")
        messages = await store.get_messages("test_session_001", limit=10)
        print(f"  - 获取到 {len(messages)} 条消息")
        for msg in messages:
            print(f"    [{msg.role}] {msg.sender_name}: {msg.content[:30]}...")

        # 测试 4: 按发送者过滤消息
        print("\n[测试 4] 按发送者过滤")
        user1_messages = await store.get_messages(
            "test_session_001", sender_id="user_001"
        )
        print(f"  - 用户 user_001 的消息数: {len(user1_messages)}")

        # 测试 5: 获取会话信息
        print("\n[测试 5] 获取会话信息")
        session_info = await store.get_session("test_session_001")
        print(f"  - 消息总数: {session_info.message_count}")
        print(f"  - 参与者: {session_info.participants}")

        # 测试 6: 消息统计
        print("\n[测试 6] 消息统计")
        stats = await store.get_user_message_stats("test_session_001")
        print(f"  - 各用户消息数: {stats}")

        # 测试 7: 数据模型转换
        print("\n[测试 7] 数据模型转换")
        msg_dict = msg1.to_dict()
        print(f"  - Message.to_dict(): {list(msg_dict.keys())}")

        msg_from_dict = Message.from_dict(msg_dict)
        print(f"  - Message.from_dict() 成功: {msg_from_dict.content[:20]}...")

        # 测试 8: LLM 格式化
        print("\n[测试 8] LLM 格式化")
        llm_format = msg1.format_for_llm(include_sender_name=True)
        print(f"  - 群聊格式: {llm_format}")

        llm_format_no_name = msg1.format_for_llm(include_sender_name=False)
        print(f"  - 私聊格式: {llm_format_no_name}")

        # 测试 9: MemoryEvent 模型
        print("\n[测试 9] MemoryEvent 模型")
        memory = MemoryEvent(
            memory_content="用户张三询问了今天的天气情况",
            importance_score=0.7,
            session_id="test_session_001",
            metadata={"topic": "weather", "users": ["张三"]},
        )
        print(f"  - 记忆内容: {memory.memory_content}")
        print(f"  - 重要性: {memory.importance_score}")
        print(f"  - 是否重要: {memory.is_important(threshold=0.5)}")

        print("\n" + "=" * 60)
        print("✅ 所有测试通过!")
        print("=" * 60)

    finally:
        # 关闭数据库连接
        await store.close()
        print("\n✓ 数据库连接已关闭")

        # 清理测试数据库 (可选)
        # if os.path.exists(test_db_path):
        #     os.remove(test_db_path)
        #     print("✓ 清理测试数据库")


if __name__ == "__main__":
    print("\n运行 LivingMemory 数据层测试...\n")
    asyncio.run(test_basic_functionality())


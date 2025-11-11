"""
测试脚本 - 验证机器人记忆隔离功能

这个脚本模拟了两个机器人与同一用户的对话场景，
用于验证记忆是否正确隔离。
"""

from core.utils import get_bot_session_id


class MockEvent:
    """模拟的 AstrMessageEvent"""
    
    def __init__(self, self_id: str, unified_msg_origin: str):
        self._self_id = self_id
        self.unified_msg_origin = unified_msg_origin
        self.session_id = unified_msg_origin.split(":")[-1] if ":" in unified_msg_origin else unified_msg_origin
    
    def get_self_id(self):
        return self._self_id


def test_bot_isolation():
    """测试机器人隔离功能"""
    
    print("=" * 60)
    print("测试场景：同一用户与两个不同机器人对话")
    print("=" * 60)
    
    # 模拟场景
    user_qq = "123456789"
    bot_a_id = "111111"  # 机器人A的QQ号
    bot_b_id = "222222"  # 机器人B的QQ号
    
    # 创建两个事件（同一用户，不同机器人）
    event_a = MockEvent(
        self_id=bot_a_id,
        unified_msg_origin=f"aiocqhttp:private:{user_qq}"
    )
    
    event_b = MockEvent(
        self_id=bot_b_id,
        unified_msg_origin=f"aiocqhttp:private:{user_qq}"
    )
    
    print(f"\n用户 QQ: {user_qq}")
    print(f"机器人A QQ: {bot_a_id}")
    print(f"机器人B QQ: {bot_b_id}")
    print(f"原始会话ID: {event_a.unified_msg_origin}")
    
    # 测试1：启用机器人隔离
    print("\n" + "─" * 60)
    print("测试1：启用机器人隔离 (use_bot_isolation=True)")
    print("─" * 60)
    
    session_a_isolated = get_bot_session_id(event_a, use_bot_isolation=True)
    session_b_isolated = get_bot_session_id(event_b, use_bot_isolation=True)
    
    print(f"机器人A的会话ID: {session_a_isolated}")
    print(f"机器人B的会话ID: {session_b_isolated}")
    
    if session_a_isolated != session_b_isolated:
        print("✅ 测试通过：两个机器人的会话ID不同，记忆已隔离！")
    else:
        print("❌ 测试失败：会话ID相同，记忆未能隔离！")
    
    # 测试2：关闭机器人隔离
    print("\n" + "─" * 60)
    print("测试2：关闭机器人隔离 (use_bot_isolation=False)")
    print("─" * 60)
    
    session_a_shared = get_bot_session_id(event_a, use_bot_isolation=False)
    session_b_shared = get_bot_session_id(event_b, use_bot_isolation=False)
    
    print(f"机器人A的会话ID: {session_a_shared}")
    print(f"机器人B的会话ID: {session_b_shared}")
    
    if session_a_shared == session_b_shared:
        print("✅ 测试通过：两个机器人共享会话ID，记忆共享！")
    else:
        print("❌ 测试失败：会话ID不同，应该相同！")
    
    # 测试3：验证格式
    print("\n" + "─" * 60)
    print("测试3：验证会话ID格式")
    print("─" * 60)
    
    expected_format_a = f"bot_{bot_a_id}:{event_a.unified_msg_origin}"
    expected_format_b = f"bot_{bot_b_id}:{event_b.unified_msg_origin}"
    
    if session_a_isolated == expected_format_a:
        print(f"✅ 机器人A格式正确: {session_a_isolated}")
    else:
        print(f"❌ 机器人A格式错误")
        print(f"   期望: {expected_format_a}")
        print(f"   实际: {session_a_isolated}")
    
    if session_b_isolated == expected_format_b:
        print(f"✅ 机器人B格式正确: {session_b_isolated}")
    else:
        print(f"❌ 机器人B格式错误")
        print(f"   期望: {expected_format_b}")
        print(f"   实际: {session_b_isolated}")
    
    # 测试4：无self_id的降级场景
    print("\n" + "─" * 60)
    print("测试4：无法获取self_id的降级场景")
    print("─" * 60)
    
    class MockEventNoSelfId:
        """没有self_id的模拟事件"""
        def __init__(self, unified_msg_origin):
            self.unified_msg_origin = unified_msg_origin
            self.session_id = unified_msg_origin
        
        def get_self_id(self):
            return None  # 模拟无法获取
    
    event_no_id = MockEventNoSelfId(f"telegram:private:{user_qq}")
    session_fallback = get_bot_session_id(event_no_id, use_bot_isolation=True)
    
    print(f"原始会话ID: {event_no_id.unified_msg_origin}")
    print(f"降级后会话ID: {session_fallback}")
    
    if session_fallback == event_no_id.unified_msg_origin:
        print("✅ 测试通过：正确降级到原始会话ID")
    else:
        print("❌ 测试失败：降级行为异常")
    
    print("\n" + "=" * 60)
    print("所有测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    test_bot_isolation()

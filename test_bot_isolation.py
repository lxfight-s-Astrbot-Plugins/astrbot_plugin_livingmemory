"""
测试脚本 - 验证机器人记忆隔离功能

这个脚本模拟了两个机器人与同一用户的对话场景，
用于验证记忆是否正确隔离。
"""

from core.utils import get_bot_session_id
from core.memory_engine import _extract_session_uuid


class MockEvent:
    """模拟的 AstrMessageEvent"""
    
    def __init__(self, self_id: str, unified_msg_origin: str):
        self._self_id = self_id
        self.unified_msg_origin = unified_msg_origin
        self.session_id = unified_msg_origin.split(":")[-1] if ":" in unified_msg_origin else unified_msg_origin
    
    def get_self_id(self):
        return self._self_id


def test_uuid_extraction():
    """测试 UUID 提取函数是否正确处理复合会话ID"""
    
    print("=" * 60)
    print("测试UUID提取函数 (_extract_session_uuid)")
    print("=" * 60)
    
    test_cases = [
        ("bot_111111:aiocqhttp:private:333333", "bot_111111:aiocqhttp:private:333333", "机器人隔离格式（应保持完整）"),
        ("bot_222222:telegram:private:444444", "bot_222222:telegram:private:444444", "另一个机器人隔离格式"),
        ("aiocqhttp:private:333333", "333333", "普通格式（应提取UUID）"),
        ("telegram:group:555555", "555555", "群聊格式（应提取UUID）"),
        ("123456", "123456", "纯UUID格式"),
    ]
    
    all_passed = True
    
    for input_id, expected, description in test_cases:
        result = _extract_session_uuid(input_id)
        passed = result == expected
        all_passed = all_passed and passed
        
        status = "✅" if passed else "❌"
        print(f"\n{status} {description}")
        print(f"   输入: {input_id}")
        print(f"   期望: {expected}")
        print(f"   实际: {result}")
    
    if all_passed:
        print("\n✅ 所有UUID提取测试通过！")
    else:
        print("\n❌ 部分UUID提取测试失败！")
    
    return all_passed


def test_bot_isolation():
    """测试机器人隔离功能"""
    
    print("\n" + "=" * 60)
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
    
    # 验证：生成的会话ID不同
    if session_a_isolated != session_b_isolated:
        print("✅ 会话ID不同，记忆已隔离！")
    else:
        print("❌ 会话ID相同，记忆未能隔离！")
        return False
    
    # 验证：UUID提取后仍然不同（关键！）
    uuid_a = _extract_session_uuid(session_a_isolated)
    uuid_b = _extract_session_uuid(session_b_isolated)
    
    print(f"\nUUID提取验证:")
    print(f"机器人A提取后: {uuid_a}")
    print(f"机器人B提取后: {uuid_b}")
    
    if uuid_a != uuid_b:
        print("✅ UUID提取后仍然不同，存储和检索都能正确隔离！")
    else:
        print("❌ UUID提取后相同，这会导致记忆混淆！")
        return False
    
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
        return False
    
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
        return False
    
    if session_b_isolated == expected_format_b:
        print(f"✅ 机器人B格式正确: {session_b_isolated}")
    else:
        print(f"❌ 机器人B格式错误")
        print(f"   期望: {expected_format_b}")
        print(f"   实际: {session_b_isolated}")
        return False
    
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
        return False
    
    return True


if __name__ == "__main__":
    print("🧪 开始测试机器人记忆隔离功能\n")
    
    # 先测试UUID提取
    uuid_test_passed = test_uuid_extraction()
    
    # 再测试机器人隔离
    bot_test_passed = test_bot_isolation()
    
    print("\n" + "=" * 60)
    if uuid_test_passed and bot_test_passed:
        print("✅ 所有测试通过！机器人记忆隔离功能正常工作。")
    else:
        print("❌ 部分测试失败！请检查实现。")
    print("=" * 60)


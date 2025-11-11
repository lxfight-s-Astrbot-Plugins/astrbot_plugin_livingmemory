---
outline: deep
---

# AstrBotMessage

AstrBot 消息对象, 它是一个消息的容器, 所有平台的消息在接收时都被转换为该类型的对象, 以实现不同平台的统一处理。

对于每个事件, 一定都有一个驱动该事件的 AstrBotMessage 对象。

```mermaid
平台发来的消息 --> AstrBotMessage --> AstrBot 事件
```

### 属性

1. `type(MessageType)`: 消息类型, 参考: [MessageType](./message_type.md)
2. `self_id(str)`: 机器人自身 id, 例如在 aiocqhttp 平台, 它是机器人自身的 qq 号
3. `session_id(str)`: 不包含平台的会话 id, 以 qq 平台为例, 在私聊中它是对方 qq 号, 在群聊中它是群号
4. `message_id(str)`: 消息 id, 消息的唯一标识符, 用于引用或获取某一条消息
5. `group_id(str)`: 群组 id, 如果为私聊, 则为空字符串
6. `sender(MessageMember)`: 消息发送者, 参考: [MessageMember](./message_member.md)
7. `message(List[BaseMessageComponent])`: 消息链(Nakuru 格式), 包含该事件中的所有消息内容, 参考: [BaseMessageComponent(施工中)]
8. `message_str(str)`: 纯文本消息字符串, 相当于把消息链转换为纯文本(会丢失信息!)
9. `raw_message(object)`: 原始消息对象, 包含所有消息的原始数据(平台适配器发来的)
10. `timestamp(int)`: 消息的时间戳(会自动初始化)

---
outline: deep
---

# MessageMember

消息发送者对象, 用于标记一个消息发送者的最基本信息

### 属性

1. `user_id(str)`: 消息发送者 id, 唯一, 例如在 aiocqhttp 平台, 它是发送者的 qq 号
2. `nickname(str)`: 昵称, 例如在 aiocqhttp 平台, 它是发送者的 qq 昵称, 它会被自动初始化

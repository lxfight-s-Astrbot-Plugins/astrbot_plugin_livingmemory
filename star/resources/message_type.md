---
outline: deep
---

# MessageType

消息类型, 用于区分消息是私聊还是群聊消息, 继承自`Enum`枚举类型

使用方法如下:

```Python
from astrbot.api.platform import MessageType
print(MessageType.GROUP_MESSAGE)
```

### 内容

1. `GROUP_MESSAGE`: 群聊消息
2. `FRIEND_MESSAGE`: 私聊消息
3. `OTHER_MESSAGE`: 其他消息, 例如系统消息等

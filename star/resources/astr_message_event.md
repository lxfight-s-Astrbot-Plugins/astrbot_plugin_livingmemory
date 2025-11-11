---
outline: deep
---

## AstrMessageEvent

AstrBot 事件, AstrBot 运行的核心, AstrBot 所有操作的运行都是事件驱动的。
在插件中, 你声明的每一个`async def`函数都是一个 Handler, 它应当是一个异步协程(无 yield 返回)或异步生成器(存在一个或多个 yield)， 所有 Handler 都需要在 AstrBot 事件进入消息管道后, 被调度器触发, 在相应的阶段交由 Handler 处理。因此, 几乎所有操作都依赖于该事件, 你定义的大部分 Handler 都需要传入`event: AstrMessageEvent`参数。

```Python
@filter.command("helloworld")
async def helloworld(self, event: AstrMessageEvent):
    pass
```

这是一个接受`helloworld`指令, 触发对应操作的示例, 它应当被定义在**插件类下**, 一般而言, 想要 AstrBot 进行消息之类操作, 都需要依赖`event`参数。

### 属性

#### 消息

1. `message_str(str)`: 纯文本消息, 例如收到消息事件"你好", `event.message_str`将会是`"你好"`
2. `message_obj(AstrBotMessage)`: 消息对象, 参考: [AstrBotMessage](./astrbot_message.md)
3. `is_at_or_wake_command(bool)`: 是否@了机器人/消息带有唤醒词/为私聊(插件注册的事件监听器会让 is_wake 设为 True, 但是不会让这个属性置为 True)

#### 消息来源

4. `role(str)`: 用户是否为管理员, 两个可选选项:`"member" or "admin"`
5. `platform_meta(PlatformMetadata)`: 消息平台的信息, 参考: [PlatformMetadata](./platform_metadata.md)
6. `session_id(str)`: 不包含平台的会话 id, 以 qq 平台为例, 在私聊中它是对方 qq 号, 在群聊中它是群号, 它无法标记具体平台, 建议直接使用 9 中的`unified_msg_origin`作为代替
7. `session(MessageSession)`: 会话对象, 用于唯一识别一个会话, `unified_msg_origin`是它的字符串表示, `session_id`等价于`session.session_id`
8. `unified_msg_origin(str)`: 会话 id, 格式为: `platform_name:message_type:session_id`, 建议使用

#### 事件控制

9. `is_wake(bool)`: 机器人是否唤醒(通过 WakingStage, 详见: [WakingStage(施工中)]), 如果机器人未唤醒, 将不会触发后面的阶段
10. `call_llm(bool)`: 是否在此消息事件中禁止默认的 LLM 请求, 对于每个消息事件, AstrBot 会默认调用一次 LLM 进行回复

### 方法

#### 消息相关

1. get_message_str

```Python
get_message_str() -> str
# 等同于self.message_str
```

该方法用于获取该事件的文本消息字符串。

2. get_message_outline

```Python
get_message_outline() -> str
```

该方法用于获取消息概要, 不同于 2, 它不会忽略其他消息类型(如图片), 而是会将其他消息类型转换为对应的占位符, 例如图片会被转换为`"[图片]"`

3. get_messages

```Python
get_messages() -> List[BaseMessageComponent]
```

该方法返回一个消息列表，包含该事件中的所有消息组件。该列表中的每个组件都可以是文本、图片或其他类型的消息。组件参考: [BaseMessageComponent(施工中)]

4. get_message_type

```Python
get_message_type() -> MessageType
```

该方法用于获取消息类型, 消息类型参考: [MessageType](./message_type.md)

5. is_private_chat

```Python
is_private_chat() -> bool
```

该方法用于判断该事件是否由私聊触发

6. is_admin

```Python
is_admin()
# 等同于self.role == "admin"
```

该方法用于判断该事件是否为管理员发出

#### 消息平台相关

7. get_platform_name

```Python
get_platform_name() -> str
# 等同于self.platform_meta.name
```

该方法用于获取该事件的平台名称, 例如`"aiocqhttp"`。
如果你的插件想只对某个平台的消息事件进行处理, 可以通过该方法获取平台名称进行判断。

#### ID 相关

8. get_self_id

```Python
get_self_id() -> str
```

该方法用于获取 Bot 自身 id(自身 qq 号)

9. get_sender_id

```Python
get_sender_id() -> str
```

该方法用于获取该消息发送者 id(发送者 qq 号)

10. get_sender_name

```Python
get_sender_name() -> str
```

该方法用于获取消息发送者的昵称(可能为空)

11. get_group_id

```Python
get_group_id() -> str
```

该方法用于获取群组 id(qq 群群号), 如果不是群组消息将放回 None

#### 会话控制相关

12. get_session_id

```Python
get_session_id() -> str
# 等同于self.session_id或self.session.session_id
```

该方法用于获取当前会话 id, 格式为 `platform_name:message_type:session_id`

13. get_group

```Python
get_group(group_id: str = None, **kwargs) -> Optional[Group]
```

该方法用于获取一个群聊的数据, 如果不填写`group_id`, 默认返回当前群聊消息, 在私聊中如果不填写该参数将返回 None

仅适配 gewechat 与 aiocqhttp

#### 事件状态

14. is_wake_up

```Python
is_wake_up() -> bool
# 等同于self.is_wake
```

该方法用于判断该事件是否唤醒 Bot

15. stop_event

```Python
stop_event()
```

该方法用于终止事件传播, 调用该方法后, 该事件将停止后续处理

16. continue_event

```Python
continue_event()
```

该方法用于继续事件传播, 调用该方法后, 该事件将继续后续处理

17. is_stopped

```Python
is_stopped() -> bool
```

该方法用于判断该事件是否已经停止传播

#### 事件结果

18. set_result

```Python
set_result(result: Union[MessageEventResult, str])
```

该方法用于设置该消息事件的结果, 该结果是 Bot 发送的内容
它接受一个参数:

- result: MessageEventResult(参考:[MessageEventResult(施工中)]) 或字符串, 若为字符串, Bot 会发送该字符串消息

19. get_result

```Python
get_result() -> MessageEventResult
```

该方法用于获取消息事件的结果, 该结果类型参考: [MessageEventResult(施工中)]

20. clear_result

```Python
clear_result()
```

该方法用于清除消息事件的结果

#### LLM 相关

21. should_call_llm

```Python
should_call_llm(call_llm: bool)
```

该方法用于设置是否在此消息事件中禁止默认的 LLM 请求
只会阻止 AstrBot 默认的 LLM 请求(即收到消息->请求 LLM 进行回复)，不会阻止插件中的 LLM 请求

22. request_llm

```Python
request_llm(prompt: str,
        func_tool_manager=None,
        session_id: str = None,
        image_urls: List[str] = [],
        contexts: List = [],
        system_prompt: str = "",
        conversation: Conversation = None,
        ) -> ProviderRequest
```

该方法用于创建一个 LLM 请求

接受 7 个参数:

- prompt(str): 提示词
- func_tool_manager(FuncCall): 函数工具管理器, 参考: [FuncCall(施工中)]
- session_id(str): 已经过时, 留空即可
- image_urls(List(str)): 发送给 LLM 的图片, 可以为 base64 格式/网络链接/本地图片路径
- contexts(List): 当指定 contexts 时, 将使用其中的内容作为该次请求的上下文(而不是聊天记录)
- system_prompt(str): 系统提示词
- conversation(Conversation): 可选, 在指定的对话中进行 LLM 请求, 将使用该对话的所有设置(包括人格), 结果也会被保存到对应的对话中

#### 发送消息相关

一般作为生成器返回, 让调度器执行相应操作:

```Python
yield event.func()
```

23. make_result

```Python
make_result() -> MessageEventResult
```

该方法用于创建一个空的消息事件结果

24. plain_result

```Python
plain_result(text: str) -> MessageEventResult
```

该方法用于创建一个空的消息事件结果, 包含文本消息:text

25. image_result

```Python
image_result(url_or_path: str) -> MessageEventResult
```

该方法用于创建一个空的消息事件结果, 包含一个图片消息, 其中参数`url_or_path`可以为图片网址或本地图片路径

26. chain_result

```Python
chain_result(chain: List[BaseMessageComponent]) -> MessageEventResult
```

该方法用于创建一个空的消息事件结果, 包含整个消息链, 消息链是一个列表, 按顺序包含各个消息组件, 消息组件参考: [BaseMessageComponent(施工中)]

27. send

```Python
send(message: MessageChain)
```

注意这个方法不需要使用 yield 方式作为生成器返回来调用, 请直接使用`await event.send(message)`
该方法用于发送消息到该事件的当前对话中

接受 1 个参数:

- message(MessageChain): 消息链, 参考: [MessageChain(施工中)]

#### 其他

28. set_extra

```Python
set_extra(key, value)
```

该方法用于设置事件的额外信息, 如果你的插件需要分几个阶段处理事件, 你可以在这里将额外需要传递的信息存储入事件
接受两个参数:

- key(str): 键名
- value(any): 值

需要和 12 一起使用

29. get_extra

```Python
get_extra(key=None) -> any
```

该方法用于获取 11 中设置的额外信息, 如果没有提供键名将返回所有额外信息, 它是一个字典。

30. clear_extra

```Python
clear_extra()
```

该方法用于清除该事件的所有额外信息

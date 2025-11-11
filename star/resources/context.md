---
outline: deep
---

## Context

暴露给插件的上下文, 该类的作用就是为插件提供接口和数据。

### 属性:

1. `provider_manager`: 供应商管理器对象
2. `platform_manager`: 平台管理器对象

### 方法:

#### 插件相关

1. get_registered_star

```Python
get_registered_star(star_name: str) -> StarMetadata
```

该方法根据输入的插件名获取插件的元数据对象, 该对象包含了插件的基本信息, 例如插件的名称、版本、作者等。
该方法可以获取其他插件的元数据。
StarMetadata 详情见[StarMetadata](/dev/star/resources/star_metadata.md)

2. get_all_stars

```Python
get_all_stars() -> List[StarMetadata]
```

该方法获取所有已注册的插件的元数据对象列表, 该列表包含了所有插件的基本信息。
StarMetadata 详情见[StarMetadata](/dev/star/resources/star_metadata.md)

#### 函数工具相关

3. get_llm_tool_manager

```Python
get_llm_tool_manager() -> FuncCall
```

该方法获取 FuncCall 对象, 该对象用于管理注册的所有函数调用工具。

4. activate_llm_tool

```Python
activate_llm_tool(name: str) -> bool
```

该方法用于激活指定名称的**已经注册**的函数调用工具, 已注册的函数调用工具**默认为激活状态**, 不需要手动激活。
如果没能找到指定的函数调用工具, 则返回`False`。

5. deactivate_llm_tool

```Python
deactivate_llm_tool(name: str) -> bool
```

该方法用于停用指定名称的**已经注册**的函数调用工具。
如果没能找到指定的函数调用工具, 则返回`False`。

#### 供应商相关

6. register_provider

```Python
register_provider(provider: Provider)
```

该方法用于注册一个新**用于文本生成的**的供应商对象, 该对象必须是 Provider 类。
**用于文本生成的**的 Provider 类型为 Chat_Completion, 后面将不再重复。

7. get_provider_by_id

```Python
get_provider_by_id(provider_id: str) -> Provider
```

该方法根据输入的供应商 ID 获取供应商对象。

8. get_all_providers

```Python
get_all_providers() -> List[Provider]
```

该方法获取所有已注册的**用于文本生成的**供应商对象列表。

9. get_all_tts_providers

```Python
get_all_tts_providers() -> List[TTSProvider]
```

该方法获取所有已注册的**文本到语音**供应商对象列表。

10. get_all_stt_providers

```Python
get_all_stt_providers() -> List[STTProvider]
```

该方法获取所有已注册的**语音到文本**供应商对象列表。

11. get_using_provider

```Python
get_using_provider() -> Provider
```

该方法获取当前使用的**用于文本生成的**供应商对象。

12. get_using_tts_provider

```Python
get_using_tts_provider() -> TTSProvider
```

该方法获取当前使用的**文本到语音**供应商对象。

13. get_using_stt_provider

```Python
get_using_stt_provider() -> STTProvider
```

该方法获取当前使用的**语音到文本**供应商对象。

#### 其他

14. get_config

```Python
get_config() -> AstrBotConfig
```

该方法获取当前 AstrBot 的配置对象, 该对象包含了插件的所有配置项与 AstrBot Core 的所有配置项(谨慎修改!)。

15. get_db

```Python
get_db() -> BaseDatabase
```

该方法获取 AstrBot 的数据库对象, 该对象用于访问数据库, 该对象是 BaseDatabase 类的实例。

16. get_event_queue

```Python
get_event_queue() -> Queue
```

该方法用于获取 AstrBot 的事件队列, 这是一个异步队列, 其中的每一项都是一个 AstrMessageEvent 对象。

17. get_platform

```Python
get_platform(platform_type: Union[PlatformAdapterType, str]) -> Platform
```

该方法用于获取指定类型的平台适配器对象。

18. send_message

```Python
send_message(session: Union[str, MessageSesion], message_chain: MessageChain) -> bool
```

该方法可以根据会话的唯一标识符-session(unified_msg_origin)主动发送消息。

它接受两个参数：

- session: 会话的唯一标识符, 可以是字符串或 MessageSesion 对象， 获取该标识符参考：[获取会话的 session]。
- message_chain: 消息链对象, 该对象包含了要发送的消息内容, 该对象是 MessageChain 类的实例。

该方法返回一个布尔值, 表示是否找到对应的消息平台。

- **注意: 该方法不支持 qq_official 平台!!**

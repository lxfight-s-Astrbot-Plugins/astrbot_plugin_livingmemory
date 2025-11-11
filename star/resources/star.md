---
outline: deep
---

## Star

插件的基类, **所有插件都继承于该类**, 拥有该类的所有属性和方法。

### 属性:

1. `context`: 暴露给插件的上下文, 参考: [Context](/dev/star/resources/context.md)

### 方法:

#### 文转图

1. text_to_image

```Python
text_to_image(text: str, return_url=True) -> str
```

该方法用于**将文本转换为图片**, 如果你的插件想实现类似功能, 优先考虑使用该方法。

它接受两个参数:

- text: 你想转换为图片的文本信息, 它是一个字符串, 推荐使用多行字符串的形式。
- return_url: 返回图片链接(True)或文件路径(False)。

#### html 渲染

2. html_render

```Python
async def html_render(self, tmpl: str, data: dict, return_url: bool = True, options: dict = None) -> str:
```

该方法用于将 Jinja2 模板渲染为图片。

**参数说明:**

- `tmpl: str` (必选)
  - 描述：HTML Jinja2 模板的文件路径。
- `data: dict` (必选)
  - 描述：用于渲染模板的数据字典。
- `return_url: bool` (可选, 默认为 `True`)
  - 描述：决定返回值是图片的 URL (`True`) 还是本地文件路径 (`False`)。
- `options: dict` (可选)
  - 描述：一个包含截图详细选项的字典。
    - `timeout: float`: 截图超时时间（秒）。
    - `type: str`: 图片类型，`"jpeg"` 或 `"png"`。
    - `quality: int`: 图片质量 (0-100)，仅用于 jpeg。
    - `omit_background: bool`: 是否使用透明背景，仅用于 png。
    - `full_page: bool`: 是否截取整个页面，默认为 `True`。
    - `clip: dict`: 裁剪区域，包含 `x`, `y`, `width`, `height`。
    - `animations: str`: CSS 动画，`"allow"` 或 `"disabled"`。
    - `caret: str`: 文本光标，`"hide"` 或 `"initial"`。
    - `scale: str`: 页面缩放，`"css"` 或 `"device"`。
    - `mask: list`: 需要遮盖的 Playwright Locator 列表。

如果你不知道如何构造模板, 请参考: [Jinja2 文档](https://docs.jinkan.org/docs/jinja2/)

该功能由 [CampuxUtility](https://github.com/idoknow/CampuxUtility) 提供支持
#### 终止

3. terminate(Abstract)

> 该方法为基类提供的抽象方法, 你需要在自己的插件中实现该方法!!

该方法用于插件禁用、重载, 或关闭 AstrBot 时触发, 用于释放插件资源, 如果你的插件对 AstrBot 本体做了某些更改(例如修改了 System Prompt), 强烈建议在该方法中恢复对应的修改!! 如果你的插件使用了外部进程, 强烈建议在该方法中进行销毁!!

你需要在你的插件类中如此实现该方法:

```Python
async def terminate(self):
    """
    此处实现你的对应逻辑, 例如销毁, 释放某些资源, 回滚某些修改。
    """
```

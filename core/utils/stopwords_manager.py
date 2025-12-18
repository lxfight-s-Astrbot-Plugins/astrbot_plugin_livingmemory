"""
停用词管理器 - 管理停用词表
"""

from pathlib import Path

from astrbot.api import logger


class StopwordsManager:
    """停用词管理器"""

    def __init__(
        self,
        stopwords_dir: str | None = None,
    ):
        """
        初始化停用词管理器

        Args:
            stopwords_dir: 停用词文件存储目录（可选，如果未提供则使用内置停用词）
        """
        # 获取内置停用词目录（仓库中的 static/stopwords）
        self.builtin_stopwords_dir = Path(__file__).parent.parent.parent / "static" / "stopwords"

        # 用户自定义停用词目录（用于保存用户添加的停用词）
        if stopwords_dir:
            self.custom_stopwords_dir = Path(stopwords_dir)
            self.custom_stopwords_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.custom_stopwords_dir = None

        self.stopwords: set[str] = set()
        self.custom_stopwords: set[str] = set()

    async def load_stopwords(
        self,
        source: str = "hit",
        custom_words: list | None = None,
    ) -> set[str]:
        """
        加载停用词表

        Args:
            source: 停用词来源 ("hit" 或自定义文件路径)
            custom_words: 用户自定义停用词列表

        Returns:
            Set[str]: 停用词集合
        """
        logger.info(f"开始加载停用词表: source={source}")

        # 1. 加载标准停用词表
        if source == "hit":
            # 从仓库内置目录加载
            filename = f"stopwords_{source}.txt"
            filepath = self.builtin_stopwords_dir / filename

            if filepath.exists():
                self.stopwords = await self._load_from_file(filepath)
                logger.info(f"从内置目录加载停用词: {filepath}")
            else:
                logger.warning(f"内置停用词文件不存在: {filepath}，使用后备停用词")
                self.stopwords = self._get_builtin_stopwords()
        else:
            # 使用自定义文件路径
            custom_path = Path(source)
            if custom_path.exists():
                self.stopwords = await self._load_from_file(custom_path)
            else:
                logger.error(f"自定义停用词文件不存在: {source}")
                self.stopwords = self._get_builtin_stopwords()

        # 2. 添加用户自定义停用词
        if custom_words:
            self.custom_stopwords = set(custom_words)
            self.stopwords.update(self.custom_stopwords)
            logger.info(f"添加自定义停用词: {len(custom_words)} 个")

        logger.info(f"停用词表加载完成，共 {len(self.stopwords)} 个词")
        return self.stopwords


    async def _load_from_file(self, filepath: Path) -> set[str]:
        """
        从文件加载停用词

        Args:
            filepath: 文件路径

        Returns:
            Set[str]: 停用词集合
        """
        try:
            with open(filepath, encoding="utf-8") as f:
                stopwords = set()
                for line in f:
                    word = line.strip()
                    if word and not word.startswith("#"):  # 跳过空行和注释
                        stopwords.add(word)

            logger.debug(f"从文件加载停用词: {filepath}, 共 {len(stopwords)} 个")
            return stopwords

        except Exception as e:
            logger.error(f"读取停用词文件失败: {filepath}, 错误: {e}")
            return set()

    def _get_builtin_stopwords(self) -> set[str]:
        """
        获取内置的基础停用词表（作为后备方案）

        Returns:
            Set[str]: 基础停用词集合
        """
        # 精简的核心停用词列表
        builtin = {
            # 代词
            "我",
            "你",
            "他",
            "她",
            "它",
            "我们",
            "你们",
            "他们",
            "她们",
            "它们",
            "自己",
            "自家",
            "咱",
            "咱们",
            "这",
            "那",
            "这个",
            "那个",
            "这些",
            "那些",
            # 助词
            "的",
            "了",
            "着",
            "过",
            "地",
            "得",
            "呢",
            "吗",
            "吧",
            "啊",
            "呀",
            # 连词
            "和",
            "与",
            "及",
            "以及",
            "或",
            "或者",
            "还是",
            "而",
            "且",
            "并",
            "但",
            "但是",
            "然而",
            "可是",
            "不过",
            "而且",
            "并且",
            # 介词
            "在",
            "从",
            "向",
            "往",
            "到",
            "由",
            "为",
            "对",
            "关于",
            "按照",
            "根据",
            "通过",
            "经过",
            "沿着",
            "朝",
            "通过",
            # 副词
            "很",
            "太",
            "非常",
            "极",
            "十分",
            "最",
            "更",
            "挺",
            "特别",
            "尤其",
            "都",
            "也",
            "还",
            "再",
            "又",
            "就",
            "才",
            "已",
            "曾",
            "已经",
            "正在",
            "将",
            "将要",
            "总是",
            "一直",
            "从来",
            # 量词
            "个",
            "只",
            "件",
            "条",
            "张",
            "把",
            "块",
            "片",
            "次",
            "遍",
            "些",
            "点",
            "下",
            "回",
            "趟",
            # 叹词
            "哦",
            "啊",
            "呀",
            "哎",
            "唉",
            "嗯",
            "哼",
            "嘿",
            # 其他虚词
            "是",
            "有",
            "没",
            "没有",
            "不",
            "没",
            "别",
            "莫",
            "等",
            "等等",
            "之",
            "所",
            "其",
            "此",
            "于",
            "让",
            "被",
            "把",
            "给",
            # 标点和符号（处理后的）
            "、",
            "，",
            "。",
            "！",
            "？",
            "；",
            "：",
            "……",
            "—",
        }

        logger.warning(f"使用内置停用词表（后备方案），共 {len(builtin)} 个词")
        return builtin

    def add_custom_stopwords(self, words: list):
        """
        添加自定义停用词

        Args:
            words: 停用词列表
        """
        if words:
            self.custom_stopwords.update(words)
            self.stopwords.update(words)
            logger.info(f"添加 {len(words)} 个自定义停用词")

    def remove_stopwords(self, words: list):
        """
        从停用词表中移除指定词

        Args:
            words: 要移除的词列表
        """
        if words:
            for word in words:
                self.stopwords.discard(word)
                self.custom_stopwords.discard(word)
            logger.info(f"移除 {len(words)} 个停用词")

    def is_stopword(self, word: str) -> bool:
        """
        检查是否为停用词

        Args:
            word: 待检查的词

        Returns:
            bool: 是否为停用词
        """
        return word in self.stopwords

    def filter_stopwords(self, tokens: list) -> list:
        """
        过滤停用词

        Args:
            tokens: 分词列表

        Returns:
            list: 过滤后的分词列表
        """
        return [token for token in tokens if token not in self.stopwords]

    async def save_custom_stopwords(self, filepath: Path | None = None):
        """
        保存自定义停用词到文件

        Args:
            filepath: 保存路径，默认为用户自定义目录下的 custom_stopwords.txt
        """
        if not filepath:
            if self.custom_stopwords_dir:
                filepath = self.custom_stopwords_dir / "custom_stopwords.txt"
            else:
                logger.warning("未设置自定义停用词目录，无法保存")
                return

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                for word in sorted(self.custom_stopwords):
                    f.write(f"{word}\n")

            logger.info(f"自定义停用词已保存到: {filepath}")

        except Exception as e:
            logger.error(f"保存自定义停用词失败: {e}")

    async def get_stopwords(self, source: str = "hit") -> str | None:
        """
        获取停用词文件路径。

        从仓库内置目录返回停用词文件路径。

        Args:
            source: 停用词来源 ("hit")

        Returns:
            停用词文件的绝对路径字符串；若发生异常则返回 None。
        """
        try:
            filename = f"stopwords_{source}.txt"
            filepath = self.builtin_stopwords_dir / filename

            # 检查内置文件是否存在
            if filepath.exists():
                return str(filepath)
            else:
                logger.warning(f"内置停用词文件不存在: {filepath}")
                return None
        except Exception as e:
            logger.error(f"获取停用词文件失败: {e}")
            return None


# 全局单例
_stopwords_manager: StopwordsManager | None = None


def get_stopwords_manager() -> StopwordsManager:
    """获取全局停用词管理器单例"""
    global _stopwords_manager
    if _stopwords_manager is None:
        _stopwords_manager = StopwordsManager()
    return _stopwords_manager

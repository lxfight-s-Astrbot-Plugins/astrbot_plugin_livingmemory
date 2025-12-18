"""
文本处理器 - 提供统一的分词和文本预处理功能
用于支持 BM25 稀疏检索和记忆内容处理
"""

import re
import string
from collections import Counter
from pathlib import Path

try:
    import jieba

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


class TextProcessor:
    """
    文本处理器

    提供统一的文本分词、清洗、停用词过滤等功能。
    主要用于:
    1. 记忆存储时的内容分词
    2. 用户查询时的查询分词
    3. BM25 算法的词频统计
    """

    # 内置的基础停用词表（后备方案）
    DEFAULT_STOPWORDS = {
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
        "哪",
        "哪个",
        "哪些",
        "谁",
        "什么",
        "怎么",
        "怎样",
        "多少",
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
        "哇",
        "哦",
        "嗯",
        "啦",
        "嘛",
        "呗",
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
        "因此",
        "所以",
        "因为",
        "由于",
        "如果",
        "假如",
        "虽然",
        "尽管",
        "除非",
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
        "朝着",
        "沿",
        "用",
        "以",
        "按",
        "依",
        "凭",
        "靠",
        "当",
        "于",
        "比",
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
        "刚",
        "刚才",
        "马上",
        "立刻",
        "顿时",
        "忽然",
        "突然",
        "渐渐",
        "逐渐",
        "慢慢",
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
        "番",
        "场",
        "阵",
        "样",
        "种",
        # 叹词
        "哦",
        "啊",
        "呀",
        "哎",
        "唉",
        "嗯",
        "哼",
        "嘿",
        "哈",
        # 动词（常用虚词性动词）
        "是",
        "有",
        "为",
        "在",
        "到",
        "去",
        "来",
        "成",
        "做",
        "看",
        "说",
        "让",
        "给",
        "被",
        "把",
        "能",
        "会",
        "要",
        "想",
        # 否定词
        "不",
        "没",
        "没有",
        "别",
        "莫",
        "勿",
        "非",
        "未",
        "无",
        # 疑问词
        "吗",
        "呢",
        "吧",
        "啊",
        "么",
        "呀",
        # 其他虚词
        "之",
        "所",
        "其",
        "此",
        "该",
        "各",
        "每",
        "某",
        "另",
        "等",
        "等等",
        "如此",
        "这样",
        "那样",
        "如何",
        "怎样",
        "多么",
        # 常见无意义词
        "一下",
        "一点",
        "一些",
        "一切",
        "一样",
        "一般",
        "一起",
        "一边",
        "上下",
        "左右",
        "前后",
        "里外",
        "东西",
        "方面",
        "时候",
        "地方",
        "样子",
        "起来",
        "出来",
        "进去",
        "过去",
        "过来",
        "下去",
        "上来",
    }

    def __init__(self, stopwords_dir: str | None = None):
        """
        初始化文本处理器

        Args:
            stopwords_dir: 停用词目录路径(将被传递给StopwordsManager),
                          如果为None则使用默认停用词
        """
        self.stopwords: set[str] = set()
        self.custom_words: set[str] = set()
        self.stopwords_dir = stopwords_dir  # 保存目录路径

        # 检查 jieba 是否可用
        if not JIEBA_AVAILABLE:
            import warnings

            warnings.warn(
                "jieba 库未安装,中文分词将受限。建议安装: pip install jieba",
                UserWarning,
            )

        # 使用默认停用词(StopwordsManager 将在需要时异步加载)
        self.stopwords = self.DEFAULT_STOPWORDS.copy()

    async def async_init(self) -> None:
        """
        异步初始化，加载停用词文件

        如果提供了 stopwords_dir，则使用 StopwordsManager 下载停用词
        """
        if self.stopwords_dir:
            from ..utils.stopwords_manager import StopwordsManager

            manager = StopwordsManager(self.stopwords_dir)
            stopwords_path = await manager.get_stopwords()
            if stopwords_path:
                self.load_stopwords(stopwords_path)

    def tokenize(self, text: str, remove_stopwords: bool = True) -> list[str]:
        """
        对单个文本进行分词

        处理流程:
        1. 文本清洗 (去除URL、标点、多余空格)
        2. 分词 (使用jieba或简单空格分词)
        3. 停用词过滤 (可选)

        Args:
            text: 待分词的文本
            remove_stopwords: 是否移除停用词

        Returns:
            分词结果列表

        Examples:
            >>> processor = TextProcessor()
            >>> processor.tokenize("我今天去图书馆看了一本很有趣的书")
            ['今天', '图书馆', '看', '本', '有趣', '书']
        """
        if not text or not text.strip():
            return []

        # 1. 清洗文本
        cleaned_text = self._clean_text(text)

        if not cleaned_text:
            return []

        # 2. 分词
        tokens = self._segment(cleaned_text)

        # 3. 过滤停用词和无效token
        filtered_tokens = []
        for token in tokens:
            # 跳过空token
            if not token or token.isspace():
                continue

            # 跳过纯标点
            if all(not c.isalnum() for c in token):
                continue

            # 跳过单字符的数字或字母（保留单字符的中文）
            if len(token) == 1 and token.isascii():
                continue

            # 停用词过滤
            if remove_stopwords and token in self.stopwords:
                continue

            filtered_tokens.append(token)

        return filtered_tokens

    def tokenize_batch(
        self, texts: list[str], remove_stopwords: bool = True
    ) -> list[list[str]]:
        """
        批量分词

        Args:
            texts: 文本列表
            remove_stopwords: 是否移除停用词

        Returns:
            分词结果列表的列表

        Examples:
            >>> processor = TextProcessor()
            >>> texts = ["文本1", "文本2", "文本3"]
            >>> results = processor.tokenize_batch(texts)
        """
        return [self.tokenize(text, remove_stopwords) for text in texts]

    def load_stopwords(self, stopwords_path: str) -> set[str]:
        """
        加载停用词表

        文件格式: 每行一个停用词,支持 # 开头的注释行

        Args:
            stopwords_path: 停用词文件路径

        Returns:
            停用词集合

        Raises:
            FileNotFoundError: 文件不存在
            IOError: 文件读取错误
        """
        path = Path(stopwords_path)

        if not path.exists():
            raise FileNotFoundError(f"停用词文件不存在: {stopwords_path}")

        try:
            with open(path, encoding="utf-8") as f:
                stopwords = set()
                for line in f:
                    word = line.strip()
                    # 跳过空行和注释
                    if word and not word.startswith("#"):
                        stopwords.add(word)

            # 合并到现有停用词
            self.stopwords.update(stopwords)

            return stopwords

        except Exception as e:
            raise OSError(f"读取停用词文件失败: {e}")

    def add_custom_words(self, words: list[str]):
        """
        添加自定义词汇到jieba词典

        用于添加领域特定词汇,提高分词准确性

        Args:
            words: 自定义词汇列表

        Examples:
            >>> processor = TextProcessor()
            >>> processor.add_custom_words(["LivingMemory", "AstrBot"])
        """
        if not JIEBA_AVAILABLE:
            import warnings

            warnings.warn("jieba 未安装,无法添加自定义词汇", UserWarning)
            return

        self.custom_words.update(words)

        for word in words:
            jieba.add_word(word)

    def add_stopwords(self, words: list[str]):
        """
        添加自定义停用词

        Args:
            words: 停用词列表
        """
        self.stopwords.update(words)

    def remove_stopwords_from_list(self, words: list[str]):
        """
        从停用词表中移除指定词

        Args:
            words: 要移除的词列表
        """
        for word in words:
            self.stopwords.discard(word)

    def get_word_freq(self, texts: list[str]) -> dict[str, int]:
        """
        统计词频

        Args:
            texts: 文本列表

        Returns:
            {词: 频次} 字典,按频次降序排列

        Examples:
            >>> processor = TextProcessor()
            >>> texts = ["我爱编程", "编程很有趣", "我也爱学习"]
            >>> freq = processor.get_word_freq(texts)
            >>> print(freq)
            {'编程': 2, '爱': 2, '有趣': 1, '学习': 1}
        """
        all_tokens = []

        # 对所有文本分词
        for text in texts:
            tokens = self.tokenize(text, remove_stopwords=True)
            all_tokens.extend(tokens)

        # 统计词频
        word_freq = Counter(all_tokens)

        # 转换为字典并按频次降序排列
        return dict(word_freq.most_common())

    def _clean_text(self, text: str) -> str:
        """
        清洗文本

        处理步骤:
        1. 移除 URL
        2. 移除英文标点
        3. 移除中文标点
        4. 移除多余空格

        Args:
            text: 原始文本

        Returns:
            清洗后的文本
        """
        # 1. 移除 URL
        text = re.sub(r"http[s]?://\S+", "", text)
        text = re.sub(r"www\.\S+", "", text)

        # 2. 移除 @mentions 和 #hashtags (常见于社交媒体)
        text = re.sub(r"@\w+", "", text)
        text = re.sub(r"#\w+", "", text)

        # 3. 移除英文标点
        text = text.translate(str.maketrans("", "", string.punctuation))

        # 4. 移除中文标点
        chinese_punctuation = (
            "！？｡。＂＃＄％＆＇（）＊＋，－／：；＜＝＞＠［＼］＾＿｀｛｜｝～"
            "｟｠｢｣､、〃《》「」『』【】〔〕〖〗〘〙〚〛〜〝〞〟〰〾〿–—"
            '‛""„‟…‧﹏'
            "·・•●○◎◇◆□■△▲▽▼⊙⊕⊗⊘⊙⊚⊛⊝⊞⊟⊠⊡⊢⊣"
        )
        text = text.translate(str.maketrans("", "", chinese_punctuation))

        # 5. 移除多余空格,保留单个空格
        text = " ".join(text.split())

        return text.strip()

    def _segment(self, text: str) -> list[str]:
        """
        对文本进行分词

        根据文本内容自动选择分词策略:
        - 包含中文: 使用 jieba 分词
        - 纯英文/数字: 按空格分词

        Args:
            text: 待分词文本

        Returns:
            分词列表
        """
        if not text:
            return []

        # 检查是否包含中文
        has_chinese = any("\u4e00" <= char <= "\u9fff" for char in text)

        if has_chinese and JIEBA_AVAILABLE:
            # 使用 jieba 分词 (搜索模式,适合检索)
            tokens = list(jieba.cut_for_search(text))
        else:
            # 按空格分词 (适用于英文或 jieba 不可用时)
            tokens = text.split()

        return tokens

    def is_stopword(self, word: str) -> bool:
        """
        检查词是否为停用词

        Args:
            word: 待检查的词

        Returns:
            是否为停用词
        """
        return word in self.stopwords

    def filter_stopwords(self, tokens: list[str]) -> list[str]:
        """
        从分词列表中过滤停用词

        Args:
            tokens: 分词列表

        Returns:
            过滤后的分词列表
        """
        return [token for token in tokens if token not in self.stopwords]

    def preprocess_for_bm25(self, text: str) -> str:
        """
        为 BM25 索引预处理文本

        返回空格分隔的 token 字符串,可直接用于 FTS5 索引

        Args:
            text: 原始文本

        Returns:
            预处理后的文本 (空格分隔的tokens)

        Examples:
            >>> processor = TextProcessor()
            >>> processor.preprocess_for_bm25("我今天去图书馆")
            "今天 图书馆"
        """
        tokens = self.tokenize(text, remove_stopwords=True)
        return " ".join(tokens)

    @property
    def stopwords_count(self) -> int:
        """获取停用词数量"""
        return len(self.stopwords)

    @property
    def custom_words_count(self) -> int:
        """获取自定义词汇数量"""
        return len(self.custom_words)


# 便捷函数
def create_text_processor(
    stopwords_path: str | None = None,
    custom_words: list[str] | None = None,
    additional_stopwords: list[str] | None = None,
) -> TextProcessor:
    """
    创建文本处理器的便捷函数

    Args:
        stopwords_path: 停用词文件路径
        custom_words: 自定义词汇列表 (添加到jieba词典)
        additional_stopwords: 额外的停用词列表

    Returns:
        配置好的 TextProcessor 实例

    Examples:
        >>> processor = create_text_processor(
        ...     stopwords_path="data/stopwords.txt",
        ...     custom_words=["LivingMemory", "AstrBot"],
        ...     additional_stopwords=["测试", "示例"]
        ... )
    """
    processor = TextProcessor(stopwords_path)

    if custom_words:
        processor.add_custom_words(custom_words)

    if additional_stopwords:
        processor.add_stopwords(additional_stopwords)

    return processor

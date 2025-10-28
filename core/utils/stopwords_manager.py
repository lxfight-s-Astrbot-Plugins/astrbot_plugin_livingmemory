# -*- coding: utf-8 -*-
"""
停用词管理器 - 自动下载和管理停用词表
"""

import aiohttp
import asyncio
from typing import Set, Optional
from pathlib import Path

from astrbot.api import logger


class StopwordsManager:
    """停用词管理器"""

    # 默认停用词表 URL
    DEFAULT_STOPWORDS_URLS = {
        "hit": "https://raw.githubusercontent.com/Northriven/Stopwords/main/stopwords_hit.txt",
        "baidu": "https://raw.githubusercontent.com/goto456/stopwords/master/baidu_stopwords.txt",
        "cn": "https://raw.githubusercontent.com/goto456/stopwords/master/cn_stopwords.txt",
    }

    # 备用 URL（使用国内镜像）
    FALLBACK_URLS = {
        "hit": "https://gitee.com/mirrors/stopwords/raw/master/stopwords_hit.txt",
    }

    def __init__(
        self,
        stopwords_dir: str = "data/plugin_data/astrbot_plugin_livingmemory/stopwords",
    ):
        """
        初始化停用词管理器

        Args:
            stopwords_dir: 停用词文件存储目录
        """
        self.stopwords_dir = Path(stopwords_dir)
        self.stopwords_dir.mkdir(parents=True, exist_ok=True)
        self.stopwords: Set[str] = set()
        self.custom_stopwords: Set[str] = set()

    async def load_stopwords(
        self,
        source: str = "hit",
        custom_words: Optional[list] = None,
        auto_download: bool = True,
    ) -> Set[str]:
        """
        加载停用词表

        Args:
            source: 停用词来源 ("hit", "baidu", "cn" 或自定义文件路径)
            custom_words: 用户自定义停用词列表
            auto_download: 如果本地文件不存在，是否自动下载

        Returns:
            Set[str]: 停用词集合
        """
        logger.info(f"开始加载停用词表: source={source}")

        # 1. 加载标准停用词表
        if source in self.DEFAULT_STOPWORDS_URLS:
            # 使用预定义的停用词源
            filename = f"stopwords_{source}.txt"
            filepath = self.stopwords_dir / filename

            if not filepath.exists() and auto_download:
                logger.info(f"本地停用词文件不存在，开始下载: {filename}")
                success = await self._download_stopwords(source, filepath)
                if not success:
                    logger.error(f"下载停用词表失败: {source}")
                    # 使用内置的基础停用词
                    self.stopwords = self._get_builtin_stopwords()
                    logger.info(f"使用内置停用词表，共 {len(self.stopwords)} 个词")
                else:
                    self.stopwords = await self._load_from_file(filepath)
            elif filepath.exists():
                self.stopwords = await self._load_from_file(filepath)
            else:
                logger.warning(f"停用词文件不存在且未启用自动下载: {filepath}")
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

        logger.info(f"✅ 停用词表加载完成，共 {len(self.stopwords)} 个词")
        return self.stopwords

    async def _download_stopwords(self, source: str, filepath: Path) -> bool:
        """
        下载停用词表

        Args:
            source: 停用词来源
            filepath: 保存路径

        Returns:
            bool: 是否成功
        """
        url = self.DEFAULT_STOPWORDS_URLS.get(source)
        if not url:
            logger.error(f"未知的停用词来源: {source}")
            return False

        # 尝试主 URL
        success = await self._download_from_url(url, filepath)

        # 如果失败，尝试备用 URL
        if not success and source in self.FALLBACK_URLS:
            logger.info("主 URL 下载失败，尝试备用 URL...")
            fallback_url = self.FALLBACK_URLS[source]
            success = await self._download_from_url(fallback_url, filepath)

        return success

    async def _download_from_url(
        self, url: str, filepath: Path, timeout: int = 30
    ) -> bool:
        """
        从 URL 下载文件

        Args:
            url: 下载链接
            filepath: 保存路径
            timeout: 超时时间（秒）

        Returns:
            bool: 是否成功
        """
        try:
            logger.debug(f"正在从 {url} 下载...")

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as response:
                    if response.status == 200:
                        content = await response.text(encoding="utf-8")

                        # 保存到文件
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(content)

                        logger.info(f"✅ 停用词表下载成功: {filepath}")
                        return True
                    else:
                        logger.error(f"下载失败，HTTP 状态码: {response.status}")
                        return False

        except asyncio.TimeoutError:
            logger.error(f"下载超时: {url}")
            return False
        except Exception as e:
            logger.error(f"下载停用词表时发生错误: {type(e).__name__}: {e}")
            return False

    async def _load_from_file(self, filepath: Path) -> Set[str]:
        """
        从文件加载停用词

        Args:
            filepath: 文件路径

        Returns:
            Set[str]: 停用词集合
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
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

    def _get_builtin_stopwords(self) -> Set[str]:
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

    async def save_custom_stopwords(self, filepath: Optional[Path] = None):
        """
        保存自定义停用词到文件

        Args:
            filepath: 保存路径，默认为 data/resources/custom_stopwords.txt
        """
        if not filepath:
            filepath = self.stopwords_dir / "custom_stopwords.txt"

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                for word in sorted(self.custom_stopwords):
                    f.write(f"{word}\n")

            logger.info(f"自定义停用词已保存到: {filepath}")

        except Exception as e:
            logger.error(f"保存自定义停用词失败: {e}")


# 全局单例
_stopwords_manager: Optional[StopwordsManager] = None


def get_stopwords_manager() -> StopwordsManager:
    """获取全局停用词管理器单例"""
    global _stopwords_manager
    if _stopwords_manager is None:
        _stopwords_manager = StopwordsManager()
    return _stopwords_manager

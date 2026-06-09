"""
备份管理处理模块
"""

from typing import TYPE_CHECKING, Any

from ..managers.backup_manager import BackupManager

if TYPE_CHECKING:
    from .utils import PageApiUtils


class BackupHandler:
    """备份管理处理器"""

    def __init__(self, utils: "PageApiUtils", data_dir: str):
        """
        初始化备份管理处理器

        Args:
            utils: PageApiUtils 工具实例
            data_dir: 数据目录路径
        """
        self.utils = utils
        self.data_dir = data_dir

    async def list_backups(self) -> dict[str, Any]:
        """
        列出所有版本备份及其元数据

        Returns:
            包含备份列表的字典
        """
        if not self.data_dir:
            return self.utils.ok({"backups": [], "total": 0})

        backups = BackupManager.list_backups(self.data_dir)
        return self.utils.ok({"backups": backups, "total": len(backups)})

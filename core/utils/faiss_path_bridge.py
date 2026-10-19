"""FAISS 非 ASCII 路径桥接（Windows）。

AstrBot 的 FAISS 封装在 Windows 上无法直接打开含非 ASCII 字符的路径
（中文用户名很常见）：``faiss.write_index`` 会报
``could not open ... for writing``，索引静默无法落盘（#284/#286）。

``core/plugin_initializer_faiss.py`` 仍会安装全局 monkey-patch；本模块提供
不依赖补丁的对称显式兜底（#290）：读写都经纯 ASCII 临时文件中转，写路径再
``os.replace`` 原子落回目标路径。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

# 打在 faiss.read_index / faiss.write_index 上的桥接标记（bridge_installed 依赖它）
_BRIDGE_MARKER = "__lmem_ascii_bridge__"


def needs_bridge(path: Any) -> bool:
    """判断路径是否需要经纯 ASCII 临时文件桥接。

    Args:
        path: 目标路径（str / bytes / os.PathLike）。

    Returns:
        bool: Windows 且路径含非 ASCII 字符时为 True。
    """
    return os.name == "nt" and not os.fspath(path).isascii()


def safe_temp_dir() -> str:
    """返回保证纯 ASCII 且可写的临时目录。

    Windows 下优先使用 ``%SystemRoot%\\Temp``：用户目录含非 ASCII 字符时
    ``tempfile.gettempdir()`` 往往落在非 ASCII 的 ``%TEMP%`` 上。

    Returns:
        str: 纯 ASCII 临时目录路径。

    Raises:
        OSError: 找不到可写的纯 ASCII 临时目录。
    """
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        temp_dir = os.path.join(root, "Temp")
        if (
            temp_dir.isascii()
            and os.path.isdir(temp_dir)
            and os.access(temp_dir, os.W_OK)
        ):
            return temp_dir
        tmp = tempfile.gettempdir()
        if tmp.isascii():
            return tmp
        raise OSError("safe_temp_dir: 无法找到可写的纯 ASCII 临时目录")
    return tempfile.gettempdir()


def safe_temp_file(prefix: str) -> str:
    """创建一个空的纯 ASCII 临时文件并返回其路径。

    Args:
        prefix: 文件名前缀，用于区分读/写中转文件。

    Returns:
        str: 临时文件路径（由调用方负责删除）。
    """
    fd, path = tempfile.mkstemp(
        prefix=f"{prefix}_", suffix=".faiss", dir=safe_temp_dir()
    )
    os.close(fd)
    return path


def _remove_quietly(path: str) -> None:
    """删除临时文件；失败时静默忽略（清理不能掩盖主流程错误）。"""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def read_index_safe(path: str):
    """读取 FAISS 索引；非 ASCII 路径经纯 ASCII 临时文件中转。

    Args:
        path: 索引文件路径。

    Returns:
        Any: ``faiss.read_index`` 返回的索引对象。
    """
    import faiss

    target = os.fspath(path)
    if not needs_bridge(target):
        return faiss.read_index(target)

    tmp = safe_temp_file("_faiss_read")
    try:
        shutil.copy2(target, tmp)
        return faiss.read_index(tmp)
    finally:
        _remove_quietly(tmp)


def write_index_safe(index: Any, path: str) -> None:
    """把 FAISS 索引写入目标路径；非 ASCII 路径经纯 ASCII 临时文件桥接。

    目标目录不存在时自动创建。需要桥接时 FAISS 只写纯 ASCII 临时文件，因此
    不依赖 ``faiss.write_index`` 的全局 monkey-patch；随后优先 ``os.replace``
    原子覆盖目标（同卷 rename），跨卷失败时退回 ``shutil.copy2`` 再删除临时文件。

    Args:
        index: ``faiss.Index`` 实例。
        path: 目标索引文件路径。
    """
    import faiss

    target = os.fspath(path)
    dirname = os.path.dirname(target)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    if not needs_bridge(target):
        faiss.write_index(index, target)
        return

    tmp = safe_temp_file("_faiss_write")
    try:
        faiss.write_index(index, tmp)
        try:
            os.replace(tmp, target)
        except OSError:
            # 跨卷（系统临时目录在 C:、索引在 D: 等）时 rename 会失败，退回复制
            shutil.copy2(tmp, target)
    finally:
        _remove_quietly(tmp)


def bridge_installed() -> bool:
    """当前进程的 ``faiss.read_index`` / ``faiss.write_index`` 是否已打桥接补丁。

    Returns:
        bool: 两个函数都带 ``__lmem_ascii_bridge__`` 标记时为 True；
            faiss 不可用或未安装补丁时为 False。
    """
    try:
        import faiss
    except Exception:
        return False
    return bool(
        getattr(faiss.read_index, _BRIDGE_MARKER, False)
        and getattr(faiss.write_index, _BRIDGE_MARKER, False)
    )

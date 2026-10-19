"""FAISS 非 ASCII 路径桥接（core/utils/faiss_path_bridge.py）测试。"""

import os
import shutil
from pathlib import Path

import faiss
import numpy as np
import pytest

from astrbot_plugin_livingmemory.core.utils import faiss_path_bridge
from astrbot_plugin_livingmemory.core.utils.faiss_path_bridge import (
    bridge_installed,
    needs_bridge,
    read_index_safe,
    safe_temp_dir,
    safe_temp_file,
    write_index_safe,
)


def _make_index(dimension: int = 4, count: int = 3):
    index = faiss.IndexIDMap(faiss.IndexFlatL2(dimension))
    index.add_with_ids(
        np.random.rand(count, dimension).astype(np.float32),
        np.arange(count, dtype=np.int64),
    )
    return index


class _AsciiOnlyFaiss:
    """模拟 Windows 上的 FAISS：非 ASCII 路径 I/O 失败，ASCII 路径转发真实 faiss。"""

    def __init__(self, monkeypatch):
        self._real_read = faiss.read_index
        self._real_write = faiss.write_index
        self.read_paths: list[str] = []
        self.write_paths: list[str] = []
        monkeypatch.setattr(faiss, "read_index", self._read)
        monkeypatch.setattr(faiss, "write_index", self._write)

    def _read(self, path, *args, **kwargs):
        path_str = os.fspath(path)
        self.read_paths.append(path_str)
        if not path_str.isascii():
            raise RuntimeError(f"could not open {path_str} for reading")
        return self._real_read(path_str, *args, **kwargs)

    def _write(self, index, path, *args, **kwargs):
        path_str = os.fspath(path)
        self.write_paths.append(path_str)
        if not path_str.isascii():
            raise RuntimeError(f"could not open {path_str} for writing")
        return self._real_write(index, path_str, *args, **kwargs)


def _force_bridge(monkeypatch):
    """强制 needs_bridge=True，跨平台稳定复现 Windows 非 ASCII 场景。"""
    monkeypatch.setattr(faiss_path_bridge, "needs_bridge", lambda _path: True)


@pytest.mark.parametrize(
    ("os_name", "path", "expected"),
    [
        ("nt", r"C:\Users\test\index.faiss", False),
        ("nt", "C:\\Users\\张三\\index.faiss", True),
        ("posix", "/home/test/index.faiss", False),
        ("posix", "/home/张三/index.faiss", False),
    ],
    ids=["nt-ascii", "nt-non-ascii", "posix-ascii", "posix-non-ascii"],
)
def test_needs_bridge_truth_table(monkeypatch, os_name, path, expected):
    monkeypatch.setattr(faiss_path_bridge.os, "name", os_name)

    assert needs_bridge(path) is expected


def test_safe_temp_file_is_created_in_ascii_temp_dir():
    path = safe_temp_file("_test_bridge")
    try:
        assert Path(path).exists()
        assert Path(path).parent == Path(safe_temp_dir())
    finally:
        os.remove(path)


def test_write_index_safe_direct_for_ascii_target(tmp_path, monkeypatch):
    """ASCII 目标：直接调用 faiss.write_index，不经临时文件中转。"""
    index = _make_index()
    target = tmp_path / "index.faiss"
    fake = _AsciiOnlyFaiss(monkeypatch)

    write_index_safe(index, str(target))

    assert target.exists()
    assert fake.write_paths == [str(target)]


def test_write_index_safe_bridges_non_ascii_target(tmp_path, monkeypatch):
    """非 ASCII 目标：FAISS 只写 ASCII 临时文件，字节最终落到目标且临时文件被清理。"""
    index = _make_index()
    target = tmp_path / "中文目录" / "index.faiss"
    fake = _AsciiOnlyFaiss(monkeypatch)
    _force_bridge(monkeypatch)

    write_index_safe(index, str(target))

    assert target.exists()
    assert fake.write_paths, "应实际调用 faiss.write_index"
    assert all(path.isascii() for path in fake.write_paths)
    assert not Path(fake.write_paths[0]).exists(), "中转临时文件应被清理"
    loaded = read_index_safe(str(target))
    assert loaded.ntotal == index.ntotal


def test_read_index_safe_bridges_non_ascii_source(tmp_path, monkeypatch):
    """非 ASCII 源文件：先复制到 ASCII 临时文件再读，临时文件被清理。"""
    index = _make_index()
    seed = tmp_path / "seed.faiss"
    faiss.write_index(index, str(seed))
    source = tmp_path / "中文目录" / "index.faiss"
    source.parent.mkdir()
    shutil.copy2(seed, source)
    fake = _AsciiOnlyFaiss(monkeypatch)
    _force_bridge(monkeypatch)

    loaded = read_index_safe(str(source))

    assert loaded.ntotal == index.ntotal
    assert fake.read_paths, "应实际调用 faiss.read_index"
    assert all(path.isascii() for path in fake.read_paths)
    assert not Path(fake.read_paths[0]).exists(), "中转临时文件应被清理"


def test_bridge_installed_false_without_marker(monkeypatch):
    monkeypatch.setattr(faiss, "read_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(faiss, "write_index", lambda *args, **kwargs: None)

    assert bridge_installed() is False


def test_bridge_installed_true_only_when_both_functions_marked(monkeypatch):
    def read_index(*args, **kwargs):
        return None

    def write_index(*args, **kwargs):
        return None

    read_index.__lmem_ascii_bridge__ = True
    write_index.__lmem_ascii_bridge__ = True
    monkeypatch.setattr(faiss, "read_index", read_index)
    monkeypatch.setattr(faiss, "write_index", write_index)
    assert bridge_installed() is True

    write_index.__lmem_ascii_bridge__ = False
    assert bridge_installed() is False

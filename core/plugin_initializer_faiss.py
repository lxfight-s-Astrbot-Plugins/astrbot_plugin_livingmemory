"""
PluginInitializer 的 InitializerFaissMixin 拆分模块
自动从 core/plugin_initializer.py 拆分，保持行为不变
"""

from .base.exceptions import InitializationError
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB  # noqa: F401 (global in _load_faiss_vec_db_class)
from astrbot.api import logger
import os
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata


class InitializerFaissMixin:
    """PluginInitializer 拆分模块：InitializerFaissMixin"""
    def _check_faiss_runtime(self) -> None:
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import faiss"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InitializationError(
                "FAISS 运行时检查失败，无法安全初始化向量数据库。"
                "请确认 faiss-cpu 已正确安装，或改用兼容当前 CPU 的 FAISS 包。"
            ) from exc

        if result.returncode == 0:
            return

        details = _faiss_error_details(result)
        if _is_faiss_binding_mismatch(details):
            version = _installed_faiss_version()
            raise InitializationError(
                "FAISS 初始化失败：Python 封装与二进制扩展不匹配"
                f"（检测到 faiss-cpu {version}）。这不是 Embedding Provider 配置问题。"
                "AstrBot Desktop 用户请升级或修复内置 Python 环境；"
                "请避免 faiss-cpu 1.14.2，并在同一环境中干净重装兼容版本"
                "（建议 1.14.3 或更高版本）。"
                f"{' 原始错误: ' + details if details else ''}"
            )

        if not _should_try_faiss_generic(result):
            raise InitializationError(
                "FAISS 初始化失败，faiss-cpu 无法在当前 Python 环境中加载。"
                "请检查安装是否完整，并确保 Python 封装与二进制扩展来自同一版本。"
                f"{' 原始错误: ' + details if details else ''}"
            )

        # Some faiss-cpu wheels select an optimized extension that is incompatible
        # with the current CPU. Probe the generic extension before changing this process.
        generic_env = os.environ.copy()
        generic_env["FAISS_OPT_LEVEL"] = "generic"
        try:
            generic_result = subprocess.run(
                [sys.executable, "-c", "import faiss"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=generic_env,
            )
        except (OSError, subprocess.TimeoutExpired):
            generic_result = None

        if generic_result is not None and generic_result.returncode == 0:
            os.environ["FAISS_OPT_LEVEL"] = "generic"
            logger.warning(
                "FAISS 默认优化扩展加载失败，已回退到 generic 指令集兼容模式。"
            )
            return

        if generic_result is not None:
            generic_details = _faiss_error_details(generic_result)
            if generic_details and generic_details != details:
                details = f"{details}；generic 模式: {generic_details}".strip("；")
        raise InitializationError(
            "FAISS 初始化失败，当前 CPU 或运行环境可能不兼容 faiss-cpu。"
            "已尝试 generic 指令集兼容模式；请重新安装兼容版本的 FAISS，"
            "或更换运行环境。"
            f"{' 原始错误: ' + details if details else ''}"
        )

    def _load_faiss_vec_db_class(self):
        global FaissVecDB
        if FaissVecDB is not None:
            return FaissVecDB

        self._check_faiss_runtime()
        try:
            import faiss as _faiss

            _orig_read = _faiss.read_index
            _orig_write = _faiss.write_index

            def _patched_read_index(path: str, *args, **kwargs):
                if isinstance(path, (str, bytes, os.PathLike)) and _needs_bridge(path):
                    tmp = _make_temp_file("_faiss_read")
                    try:
                        shutil.copy2(path, tmp)
                        return _orig_read(tmp, *args, **kwargs)
                    finally:
                        if os.path.exists(tmp):
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                return _orig_read(path, *args, **kwargs)

            def _patched_write_index(index, path, *args, **kwargs) -> None:
                # 仅在第二个参数为路径类对象 且 Windows 非 ASCII 时桥接；
                # 否则原样转发（如 VectorIOWriter / FILE* 等非路径对象）
                if isinstance(path, (str, bytes, os.PathLike)) and _needs_bridge(path):
                    dirname = os.path.dirname(path)
                    if dirname:
                        os.makedirs(dirname, exist_ok=True)
                    tmp = _make_temp_file("_faiss_write")
                    try:
                        _orig_write(index, tmp, *args, **kwargs)
                        # os.replace 原子覆盖，同卷 rename 跨卷 copy+delete
                        try:
                            os.replace(tmp, path)
                        except OSError:
                            shutil.copy2(tmp, path)
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                    finally:
                        if os.path.exists(tmp):
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                    return
                _orig_write(index, path, *args, **kwargs)

            _faiss.read_index = _patched_read_index
            _faiss.write_index = _patched_write_index

            from astrbot.core.db.vec_db.faiss_impl.vec_db import (
                FaissVecDB as LoadedFaissVecDB,
            )
        except (ImportError, ModuleNotFoundError, SystemError, OSError) as exc:
            raise InitializationError(
                "FAISS 初始化失败，无法加载 AstrBot FaissVecDB。"
                "请检查 faiss-cpu 安装状态和 CPU 指令集兼容性。"
            ) from exc

        FaissVecDB = LoadedFaissVecDB
        return LoadedFaissVecDB

    async def _check_and_fix_dimension_mismatch(self, index_path: str) -> bool:
        """
        检查 FAISS 索引维度与当前 embedding provider 维度是否一致

        当用户更换 embedding provider 后，旧索引的维度可能与新模型不匹配，
        导致 FAISS 插入时报错 "assert d == self.d"。
        此方法检测并自动删除不兼容的旧索引，让系统重建。

        Args:
            index_path: FAISS 索引文件路径
        """
        if not os.path.exists(index_path):
            return False

        # 空文件不是有效索引，直接删除让 initialize() 重建，避免 faiss 抛异常
        try:
            if os.path.getsize(index_path) == 0:
                os.remove(index_path)
                logger.debug(f"已删除空索引文件: {_sanitize_path(index_path)}")
                return True
        except OSError:
            pass

        try:
            import faiss  # noqa: F401
        except (ImportError, ModuleNotFoundError, SystemError, OSError) as exc:
            raise InitializationError(
                "FAISS 初始化失败，无法读取索引文件。"
                "请检查 faiss-cpu 安装状态和 CPU 指令集兼容性。"
            ) from exc

        # 读取索引文件 — 仅在 FAISS I/O 失败时进入坏索引处理
        try:
            old_index = self._faiss_read_index_safe(index_path)
        except InitializationError:
            raise
        except Exception as e:
            error_msg = str(e)
            # 文件在 os.path.exists 和 faiss.read_index 之间消失（如被外部进程删除），
            # 这不是坏文件，不需要隔离，让 initialize() 自动重建即可
            if "No such file" in error_msg or "could not open" in error_msg:
                logger.debug(
                    f"FAISS 索引文件({_sanitize_path(index_path)})在检查时不可访问，将由 initialize() 重建: {e}"
                )
                return False
            # 真正的坏文件：直接删除（系统会自动重建），避免累积 .corrupt_* 文件
            try:
                os.remove(index_path)
                logger.error(
                    f"FAISS 索引文件已损坏并被删除: {_sanitize_path(index_path)}。"
                    "系统将创建空索引，并在初始化后尝试分批重建。",
                    exc_info=True,
                )
            except OSError:
                logger.error(
                    f"检查索引维度时出错，且删除坏索引失败: {e}",
                    exc_info=True,
                )
            return True

        # 对比维度 — 放在坏索引处理之外，避免 embedding_provider 异常误删健康索引
        old_dim = old_index.d
        new_dim = self.embedding_provider.get_dim()  # type: ignore

        if old_dim != new_dim:
            logger.warning(
                f"检测到 FAISS 索引维度不匹配: 索引维度={old_dim}, "
                f"当前 Embedding Provider 维度={new_dim}"
            )
            logger.warning(
                "这通常由 Embedding 模型切换导致。旧索引将被删除，系统会自动重建索引。"
            )

            os.remove(index_path)
            logger.info(f"已删除不兼容的旧索引文件: {_sanitize_path(index_path)}")
            logger.info("注意: 向量检索功能将暂时不可用，直到重新导入记忆数据。")
            return True

        return False

    @staticmethod
    def _faiss_read_index_safe(index_path: str):
        """通过 ASCII 临时路径桥接 FAISS read_index。

        monkey-patch 已覆盖全局 faiss.read_index，此方法作为显式后备。
        """
        if not _needs_bridge(index_path):
            import faiss

            return faiss.read_index(index_path)
        tmp = _make_temp_file("_faiss_read")
        try:
            shutil.copy2(index_path, tmp)
            import faiss

            return faiss.read_index(tmp)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


_FAISS_GENERIC_FALLBACK_MARKERS = (
    "illegal instruction",
    "optimized",
    "avx",
    "simd",
    "dll load failed",
    "cannot open shared object file",
    "could not load library",
    "image not found",
    "symbol not found",
    "undefined symbol",
)


# 顶层辅助函数（自 plugin_initializer.py 迁移）
def _needs_bridge(path: str) -> bool:
    """判断是否需要 ASCII 临时文件桥接。"""
    path = os.fspath(path)
    return os.name == "nt" and not path.isascii()

def _safe_temp_dir() -> str:
    """返回保证纯 ASCII 且可写的临时目录。"""
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
        raise OSError("_safe_temp_dir: 无法找到可写的纯 ASCII 临时目录")
    return tempfile.gettempdir()

def _make_temp_file(prefix: str) -> str:
    """创建 Faiss 桥接临时文件，返回纯 ASCII 路径。"""
    safe_dir = _safe_temp_dir()
    fd, path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".faiss", dir=safe_dir)
    os.close(fd)
    return path

def _sanitize_path(path: str) -> str:
    """脱敏路径：非 ASCII 部分替换为 [***]，避免日志泄露中文用户名。"""
    path = os.fspath(path)
    if path.isascii():
        return path
    parts: list[str] = []
    for ch in path:
        if ch.isascii():
            parts.append(ch)
        elif not parts or parts[-1] != "[***]":
            parts.append("[***]")
    return "".join(parts)

def _faiss_error_details(result: subprocess.CompletedProcess[str]) -> str:
    """Extract useful diagnostics from a FAISS import probe."""
    details = (result.stderr or result.stdout or "").strip()
    if result.returncode < 0:
        details = f"进程被信号 {-result.returncode} 终止。{details}".strip()
    return details

def _is_faiss_binding_mismatch(details: str) -> bool:
    """Identify known Python-wrapper/binary-extension mismatches."""
    lowered = details.lower()
    return "superkmeans" in lowered or (
        "python binding" in lowered and "mismatch" in lowered
    )

def _should_try_faiss_generic(result: subprocess.CompletedProcess[str]) -> bool:
    """Return whether a generic-instruction-set probe can plausibly help."""
    if result.returncode < 0:
        return True
    details = _faiss_error_details(result).lower()
    return any(marker in details for marker in _FAISS_GENERIC_FALLBACK_MARKERS)

def _installed_faiss_version() -> str:
    """Read package metadata without importing a potentially broken FAISS module."""
    try:
        return metadata.version("faiss-cpu")
    except metadata.PackageNotFoundError:
        return "未知"


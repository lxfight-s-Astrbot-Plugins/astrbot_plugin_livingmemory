"""Tests for zero-overhead networkx probe and graceful community fallback."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from astrbot_plugin_livingmemory.core.page_api_modules import graph_handler as graph_handler_mod
from astrbot_plugin_livingmemory.main import _probe_lazy_dependencies


def test_importing_main_does_not_load_networkx():
    repo_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; "
        "from tests.conftest import _ensure_plugin_package_alias, "
        "_install_optional_dependency_stubs, _install_astrbot_stubs; "
        "_ensure_plugin_package_alias(); "
        "_install_optional_dependency_stubs(); "
        "_install_astrbot_stubs(); "
        "import astrbot_plugin_livingmemory.main; "
        "assert 'networkx' not in sys.modules, 'networkx loaded eagerly'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_main_probe_detects_missing_networkx_without_importing(
    monkeypatch: pytest.MonkeyPatch,
):
    evicted = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "networkx" or name.startswith("networkx.")
    }
    for name in evicted:
        monkeypatch.delitem(sys.modules, name)

    # Installed case: probe succeeds and networkx stays unloaded in sys.modules.
    _probe_lazy_dependencies()
    assert "networkx" not in sys.modules

    real_find_spec = importlib.util.find_spec

    def _missing_networkx_spec(name: str, package: str | None = None):
        if name == "networkx":
            return None
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", _missing_networkx_spec)
    with pytest.raises(ModuleNotFoundError) as exc_info:
        _probe_lazy_dependencies()
    assert exc_info.value.name == "networkx"


def test_assign_communities_degrades_gracefully_when_networkx_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(graph_handler_mod, "_nx", None)
    monkeypatch.setattr(graph_handler_mod, "_missing_nx_warned", False)

    def _raise_missing():
        raise ModuleNotFoundError("No module named 'networkx'", name="networkx")

    monkeypatch.setattr(graph_handler_mod, "_networkx", _raise_missing)

    snapshot = {
        "nodes": [{"id": 1}, {"id": 2}],
        "edges": [{"source": 1, "target": 2, "weight": 1.0}],
    }
    graph_handler_mod.GraphHandler._assign_communities(snapshot)
    assert [node["community"] for node in snapshot["nodes"]] == [0, 0]


def test_assign_communities_propagates_non_networkx_module_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(graph_handler_mod, "_nx", None)

    def _raise_subdep_missing():
        raise ModuleNotFoundError("No module named 'scipy'", name="scipy")

    monkeypatch.setattr(graph_handler_mod, "_networkx", _raise_subdep_missing)

    snapshot = {
        "nodes": [{"id": 1}, {"id": 2}],
        "edges": [{"source": 1, "target": 2, "weight": 1.0}],
    }
    with pytest.raises(ModuleNotFoundError) as exc_info:
        graph_handler_mod.GraphHandler._assign_communities(snapshot)
    assert exc_info.value.name == "scipy"

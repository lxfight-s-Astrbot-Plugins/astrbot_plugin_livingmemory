import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "pages" / "dashboard"


def test_dashboard_supports_japanese_locale() -> None:
    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    source = (DASHBOARD / "i18n.js").read_text(encoding="utf-8")

    assert 'data-lang="ja"' in index
    assert '"zh", "en", "ru", "ja"' in source

    msg_block = source.split("const MSG = {", 1)[1].split("const MSG_JA", 1)[0]
    ja_block = source.split("const MSG_JA = {", 1)[1].split("  };", 1)[0]
    msg_keys = set(re.findall(r'"([A-Za-z0-9_.]+)":\s*\{', msg_block))
    ja_keys = set(re.findall(r'"([A-Za-z0-9_.]+)":\s*"', ja_block))
    assert ja_keys == msg_keys, "MSG_JA 必须覆盖全部 dashboard 文案"


def test_dashboard_loads_built_assets_instead_of_uncompressed_sources() -> None:
    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert 'href="./assets/styles.min.css"' in index
    assert 'src="./assets/base.min.js"' in index
    assert 'src="./assets/graph.min.js"' in index
    assert 'src="./assets/app.min.js"' in index
    for legacy in (
        "./app.js",
        "./appearance.js",
        "./styles.css",
        "./i18n.js",
        "./graph-shared.js",
        "./graph-ui.js",
    ):
        assert legacy not in index

    for name in (
        "styles.min.css",
        "base.min.js",
        "graph.min.js",
        "app.min.js",
        "graph-layout-worker.min.js",
    ):
        assert (DASHBOARD / "assets" / name).stat().st_size > 1_000

    # 629KB 的 3D bundle 只在进入 3D 视图时按需 import，首屏不得预载
    assert 'rel="modulepreload"' not in index
    assert 'data-module="./assets/graph-3d.min.js"' in index


def test_dashboard_build_outputs_are_current() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH，跳过 dashboard 构建产物校验")
    result = subprocess.run(
        [node, "scripts/build_dashboard.mjs", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_dashboard_vendors_and_loads_lucide_subset() -> None:
    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    subset = (DASHBOARD / "vendor" / "lucide-subset.js").read_text(encoding="utf-8")

    assert '<script src="./vendor/lucide-subset.js"></script>' in index
    size = len(subset.encode("utf-8"))
    assert 1_000 < size < 50_000, f"lucide 子集应保持轻量，实际 {size} 字节"
    assert (DASHBOARD / "vendor" / "LUCIDE_LICENSE").is_file()
    assert "lucide.createIcons" in app
    assert 'data-lucide="' in index


def test_lucide_subset_covers_every_used_icon() -> None:
    subset = (DASHBOARD / "vendor" / "lucide-subset.js").read_text(encoding="utf-8")
    sources = [DASHBOARD / "index.html", *sorted((DASHBOARD / "modules").glob("*.js"))]
    used: set[str] = set()
    for source in sources:
        used |= set(
            re.findall(
                r'data-lucide="([a-z0-9-]+)"', source.read_text(encoding="utf-8")
            )
        )
    # 三目表达式动态生成的图标名不会被正则捕获，显式列出
    used |= {"trash-2", "check"}

    missing = sorted(name for name in used if f'"{name}":' not in subset)
    assert not missing, f"lucide 子集缺少图标，需要重新生成: {missing}"


def test_dashboard_has_no_handwritten_or_inline_svg_icons() -> None:
    source_files = [
        DASHBOARD / "index.html",
        DASHBOARD / "styles.css",
        DASHBOARD / "art-direction.css",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in source_files)

    assert "<svg" not in combined.lower()
    assert "data:image/svg+xml" not in combined.lower()


def test_dashboard_visible_ui_has_no_emoji() -> None:
    source_files = [
        DASHBOARD / "index.html",
        DASHBOARD / "i18n.js",
        DASHBOARD / "modules" / "prompt-page.js",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in source_files)

    emoji_ranges = (
        (0x1F000, 0x1FAFF),
        (0x2600, 0x27BF),
    )
    assert "\ufe0f" not in combined
    assert not any(
        start <= ord(character) <= end
        for character in combined
        for start, end in emoji_ranges
    )


def test_dynamic_dashboard_panels_rehydrate_lucide_icons() -> None:
    prompt_page = (DASHBOARD / "modules" / "prompt-page.js").read_text(encoding="utf-8")
    peek_panel = (DASHBOARD / "modules" / "peek-panel.js").read_text(encoding="utf-8")

    assert "lmHydrateIcons" in prompt_page
    assert "lmHydrateIcons" in peek_panel


def test_memory_transfer_controls_use_lucide_and_preview_before_import() -> None:
    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    memory_page = (DASHBOARD / "modules" / "memory-page.js").read_text(encoding="utf-8")

    assert 'id="mem-export"' in index
    assert 'data-lucide="download"' in index
    assert 'id="mem-import"' in index
    assert 'data-lucide="upload"' in index
    assert 'accept=".json,.csv,application/json,text/csv"' in index
    assert '"memories/import"' in memory_page
    assert "dry_run: true" in memory_page
    assert "dry_run: false" in memory_page


def test_graph_dashboard_defaults_to_limited_overview_and_expanded_queries() -> None:
    graph_ui = (DASHBOARD / "graph-ui.js").read_text(encoding="utf-8")

    # 默认（ensureGraphScene / 空检索）走受限概览，不传 full_graph（#248）
    assert "fetchOverview(false)" in graph_ui
    # 「全量图谱」按钮可来回切换受限概览 ↔ 全量图（#248）
    assert 'fetchOverview(state.overviewMode !== "full")' in graph_ui
    assert 'params.set("full_graph", "true")' in graph_ui
    assert "limit_memories: 24" in graph_ui
    assert "limit_entries: 80" in graph_ui
    assert "limit_nodes: 80" in graph_ui
    assert "limit_edges: 120" in graph_ui
    # 离开图谱页/切到 3D 时销毁 2D renderer 与布局 Worker，回来按需重建
    assert "releaseGraph2D()" in graph_ui
    assert "window.Graph2D.destroy()" in graph_ui
    assert "initGraph2D()" in graph_ui


def test_dashboard_bundles_are_minified() -> None:
    for name in ("base.min.js", "graph.min.js", "app.min.js"):
        source = (DASHBOARD / "assets" / name).read_text(encoding="utf-8")
        assert "/* ---- " not in source, f"{name} 不应保留源文件拼接标记（应已 minify）"
        average_line = len(source) / max(1, source.count(chr(10)))
        assert average_line > 500, (
            f"{name} 应压缩为长行（实际平均 {average_line:.0f} 字符/行）"
        )


def test_dashboard_2d_worker_is_a_single_built_file() -> None:
    graph_2d = (DASHBOARD / "graph-2d.js").read_text(encoding="utf-8")
    worker = (DASHBOARD / "assets" / "graph-layout-worker.min.js").read_text(
        encoding="utf-8"
    )

    assert "./assets/graph-layout-worker.min.js?protocol=2" in graph_2d
    assert "importScripts" not in worker, "Worker 依赖应在构建期合并"
    # destroy 时连同布局缓存一起释放（位置/社区数据随 Worker 回收）
    assert "this.animator._layoutCache = {}" in graph_2d
    assert "GraphLayoutCore" in worker or "createForceLayout" in worker
    assert "onmessage" in worker


def test_large_graph_renderer_uses_lod_and_event_driven_frames() -> None:
    graph_2d = (DASHBOARD / "graph-2d.js").read_text(encoding="utf-8")
    graph_shared = (DASHBOARD / "graph-shared.js").read_text(encoding="utf-8")
    graph_renderer = (DASHBOARD / "graph-renderer.js").read_text(encoding="utf-8")
    graph_interaction = (DASHBOARD / "graph-interaction.js").read_text(encoding="utf-8")

    assert "MASSIVE_EDGE_THRESHOLD: 12000" in graph_shared
    assert "Renderer.prototype.prepareGraph" in graph_renderer
    assert "this._communityBundles" in graph_renderer
    assert "this._structuralEdges" in graph_renderer
    assert "this._instantLayout = tier >= 2" in graph_2d
    assert "onRenderRequest" in graph_interaction
    assert "this._running = false" in graph_2d


def test_graph_canvas_state_keeps_tooltip_inside_container() -> None:
    art = (DASHBOARD / "art-direction.css").read_text(encoding="utf-8")

    # art-direction 将画布提示移到左上角（top/left 16px），必须重置 styles.css
    # 的居中 translate，否则提示框会以「左上角 -50% 宽/高」定位被容器裁剪。
    assert ".graph-canvas-state" in art
    assert "transform: none" in art


def test_dashboard_hides_inactive_panels_from_keyboard_navigation() -> None:
    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    peek_panel = (DASHBOARD / "modules" / "peek-panel.js").read_text(encoding="utf-8")

    assert 'id="modal-overlay"' not in index
    assert re.search(
        r'id="peek-panel"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-hidden="true"[^>]*inert',
        index,
    ), "peek 面板应同时声明 dialog 语义与隐藏状态"

    assert 'panel.removeAttribute("inert")' in peek_panel
    assert 'panel.setAttribute("aria-hidden", "false")' in peek_panel
    assert 'panel.setAttribute("inert", "")' in peek_panel
    assert 'panel.setAttribute("aria-hidden", "true")' in peek_panel

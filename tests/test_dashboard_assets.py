from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "pages" / "dashboard"


def test_dashboard_vendors_and_loads_lucide() -> None:
    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")

    assert '<script src="./vendor/lucide.min.js"></script>' in index
    assert (DASHBOARD / "vendor" / "lucide.min.js").stat().st_size > 100_000
    assert (DASHBOARD / "vendor" / "LUCIDE_LICENSE").is_file()
    assert "lucide.createIcons" in app
    assert 'data-lucide="' in index


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
    memory_page = (DASHBOARD / "modules" / "memory-page.js").read_text(
        encoding="utf-8"
    )

    assert 'id="mem-export"' in index
    assert 'data-lucide="download"' in index
    assert 'id="mem-import"' in index
    assert 'data-lucide="upload"' in index
    assert 'accept=".json,.csv,application/json,text/csv"' in index
    assert '"memories/import"' in memory_page
    assert "dry_run: true" in memory_page
    assert "dry_run: false" in memory_page


def test_graph_dashboard_requests_full_overview_and_expanded_queries() -> None:
    graph_ui = (DASHBOARD / "graph-ui.js").read_text(encoding="utf-8")

    assert 'full_graph: "true"' in graph_ui
    assert "limit_memories: 24" in graph_ui
    assert "limit_entries: 80" in graph_ui
    assert "limit_nodes: 80" in graph_ui
    assert "limit_edges: 120" in graph_ui


def test_large_graph_renderer_uses_lod_and_event_driven_frames() -> None:
    graph_2d = (DASHBOARD / "graph-2d.js").read_text(encoding="utf-8")
    graph_shared = (DASHBOARD / "graph-shared.js").read_text(encoding="utf-8")
    graph_renderer = (DASHBOARD / "graph-renderer.js").read_text(encoding="utf-8")
    graph_interaction = (DASHBOARD / "graph-interaction.js").read_text(
        encoding="utf-8"
    )

    assert "MASSIVE_EDGE_THRESHOLD: 12000" in graph_shared
    assert "Renderer.prototype.prepareGraph" in graph_renderer
    assert "this._communityBundles" in graph_renderer
    assert "this._structuralEdges" in graph_renderer
    assert "this._instantLayout = tier >= 2" in graph_2d
    assert "onRenderRequest" in graph_interaction
    assert "this._running = false" in graph_2d
    assert "Graph2D.prototype.getDiagnostics" in graph_2d


def test_dashboard_hides_inactive_panels_from_keyboard_navigation() -> None:
    index = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    peek_panel = (DASHBOARD / "modules" / "peek-panel.js").read_text(
        encoding="utf-8"
    )

    assert 'id="modal-overlay"' not in index
    assert 'id="peek-panel" aria-hidden="true" inert' in index
    assert 'panel.removeAttribute("inert")' in peek_panel
    assert 'panel.setAttribute("aria-hidden", "false")' in peek_panel
    assert 'panel.setAttribute("inert", "")' in peek_panel
    assert 'panel.setAttribute("aria-hidden", "true")' in peek_panel

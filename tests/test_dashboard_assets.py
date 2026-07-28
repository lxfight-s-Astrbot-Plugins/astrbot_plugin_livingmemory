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


def test_graph_dashboard_requests_full_overview_and_expanded_queries() -> None:
    graph_ui = (DASHBOARD / "graph-ui.js").read_text(encoding="utf-8")

    assert 'full_graph: "true"' in graph_ui
    assert "limit_memories: 24" in graph_ui
    assert "limit_entries: 80" in graph_ui
    assert "limit_nodes: 80" in graph_ui
    assert "limit_edges: 120" in graph_ui

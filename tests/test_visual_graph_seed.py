"""Visual fixtures use real storage while keeping existing records intact."""

import aiosqlite
import pytest
from astrbot_plugin_livingmemory.core.models.graph_models import GraphEntry, GraphNode
from astrbot_plugin_livingmemory.scripts.seed_visual_graph import seed_visual_graph
from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

from astrbot.core.db.vec_db.faiss_impl.document_storage import DocumentStorage


@pytest.mark.asyncio
async def test_seed_adds_scoped_graph_and_archived_details_without_replacing_data(
    tmp_path,
):
    path = tmp_path / "livingmemory.db"
    documents = DocumentStorage(str(path))
    await documents.initialize()
    original_id = await documents.insert_document(
        "existing", "Existing memory", {"session_id": "existing", "status": "active"}
    )
    await documents.engine.dispose()
    graph = GraphStore(str(path))
    await graph.initialize()
    original_nodes = [GraphNode("person", "Existing person", "existing-person")]
    original_node_ids = await graph.upsert_nodes(original_nodes)
    await graph.add_entries(
        [
            GraphEntry(
                "existing-entry",
                original_id,
                "existing",
                None,
                "summary",
                "Existing memory",
                node_keys=[original_nodes[0].node_key],
            )
        ],
        original_node_ids,
        {},
    )

    result = await seed_visual_graph(
        tmp_path,
        nodes=120,
        edges=600,
        memories=24,
        communities=3,
        session_id="visual-test",
    )
    snapshot = await graph.get_full_graph_snapshot(session_id="visual-test")
    assert len(snapshot["nodes"]) == result["nodes"] == 120
    assert len(snapshot["edges"]) == result["edges"] == 600
    assert len(snapshot["memories"]) == result["memories"] == 24
    assert {node["type"] for node in snapshot["nodes"]} == {
        "person",
        "topic",
        "summary",
        "fact",
    }
    assert (
        len((await graph.get_full_graph_snapshot(session_id="existing"))["nodes"]) == 1
    )
    async with aiosqlite.connect(path) as db:
        original = await (
            await db.execute("SELECT text FROM documents WHERE id = ?", (original_id,))
        ).fetchone()
        assert original[0] == "Existing memory"
        count = await (
            await db.execute(
                "SELECT count(*) FROM documents WHERE json_extract(metadata,'$.session_id') = 'visual-test' AND json_extract(metadata,'$.status') = 'archived'"
            )
        ).fetchone()
        assert count[0] == 24
    await graph.close()

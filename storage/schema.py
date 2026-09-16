"""Single source of SQLite DDL shared by runtime initialization and DB migration.

Keeping the CREATE statements in one place prevents the two code paths from
drifting: runtime `initialize()` creates tables on fresh databases, while
`DBMigration` steps must recreate the same objects on databases upgraded from
older plugin versions. All statements are idempotent (IF NOT EXISTS).
"""

from __future__ import annotations

# Graph-memory schema: 4 tables, the entries FTS index and 3 lookup indexes.
# NOTE: the trigram nodes-FTS table, its triggers and the semantic edge index
# are runtime-only (created by GraphStore.initialize) because they need a
# capability check for the SQLite trigram tokenizer; migrating databases get
# them on the next plugin start, not during migration.
GRAPH_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS graph_nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_key TEXT NOT NULL UNIQUE,
        node_type TEXT NOT NULL,
        node_value TEXT NOT NULL,
        canonical_value TEXT NOT NULL,
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        edge_key TEXT NOT NULL UNIQUE,
        source_node_id INTEGER NOT NULL,
        target_node_id INTEGER NOT NULL,
        relation_type TEXT NOT NULL,
        source_memory_id INTEGER NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        confidence REAL NOT NULL DEFAULT 0.8,
        status TEXT NOT NULL DEFAULT 'active',
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(source_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE,
        FOREIGN KEY(target_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_key TEXT NOT NULL UNIQUE,
        source_memory_id INTEGER NOT NULL,
        session_id TEXT,
        persona_id TEXT,
        entry_type TEXT NOT NULL,
        relation_type TEXT,
        content TEXT NOT NULL,
        metadata TEXT DEFAULT '{}',
        edge_id INTEGER,
        vector_doc_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(edge_id) REFERENCES graph_edges(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_entry_nodes (
        entry_id INTEGER NOT NULL,
        node_id INTEGER NOT NULL,
        PRIMARY KEY(entry_id, node_id),
        FOREIGN KEY(entry_id) REFERENCES graph_entries(id) ON DELETE CASCADE,
        FOREIGN KEY(node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS livingmemory_graph_entries_fts
    USING fts5(content, entry_id UNINDEXED, tokenize='unicode61')
    """,
    "CREATE INDEX IF NOT EXISTS idx_graph_nodes_canonical ON graph_nodes(canonical_value)",
    "CREATE INDEX IF NOT EXISTS idx_graph_edges_memory_id ON graph_edges(source_memory_id)",
    "CREATE INDEX IF NOT EXISTS idx_graph_entries_memory_id ON graph_entries(source_memory_id)",
)

# Resumable write-operation log (introduced in schema v8).
WRITE_OPS_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS memory_write_ops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        op_type TEXT NOT NULL,
        memory_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        step TEXT NOT NULL DEFAULT 'started',
        payload TEXT DEFAULT '{}',
        error TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_write_ops_status
    ON memory_write_ops(status, updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_write_ops_memory
    ON memory_write_ops(memory_id, op_type)
    """,
)

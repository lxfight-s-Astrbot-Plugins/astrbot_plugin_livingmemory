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
    # 邻居扩展按 target_node_id 反查，缺少该索引时每次召回退化为全表扫描
    "CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_node_id, status)",
    # 全量快照从 active 边里按 weight 取前 N，缺少该索引时 20 万级边表要全表过滤
    "CREATE INDEX IF NOT EXISTS idx_graph_edges_status_weight ON graph_edges(status, weight DESC)",
)

# Version bookkeeping tables are shared by the engine, migrations and validators;
# keep the DDL in one place so ownership of the schema stays unambiguous.
DB_VERSION_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS db_version (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version INTEGER NOT NULL,
        description TEXT,
        migrated_at TEXT NOT NULL,
        migration_duration_seconds REAL
    )
    """,
)

MIGRATION_STATUS_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS migration_status (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    )
    """,
)

# Expression indexes must match the filter/order expressions used by recall and
# the dashboard literally (including COALESCE/CAST wrappers) to be usable.
# These only rely on the metadata column, so migrations can apply them to legacy
# documents tables before the engine adds its compatibility columns.
DOCUMENTS_INDEX_STATEMENTS: tuple[str, ...] = (
    """
    CREATE INDEX IF NOT EXISTS idx_doc_metadata
    ON documents(json_extract(metadata, '$.session_id'))
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_doc_persona_metadata
    ON documents(json_extract(metadata, '$.persona_id'))
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_doc_importance_metadata
    ON documents(json_extract(metadata, '$.importance'))
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_doc_last_access_metadata
    ON documents(json_extract(metadata, '$.last_access_time'))
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_doc_status
    ON documents(COALESCE(json_extract(metadata, '$.status'), 'active'))
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_doc_memory_type
    ON documents(UPPER(COALESCE(json_extract(metadata, '$.memory_type'), 'GENERAL')))
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_doc_create_time
    ON documents(
        COALESCE(CAST(json_extract(metadata, '$.create_time') AS REAL), 0) DESC,
        id DESC
    )
    """,
)

# Requires the doc_id compatibility column, which the engine adds before
# executing this statement; migrations run earlier and must not use it.
DOCUMENTS_DOC_ID_INDEX_STATEMENT = """
    CREATE INDEX IF NOT EXISTS idx_documents_doc_id
    ON documents(doc_id)
    """


# User profile tables are plugin-owned (the host ``documents`` table stays
# untouched). ``scope`` is the resolved memory scope (session/user/global) and
# history keeps every applied version so manual edits and reflection updates
# remain auditable.
PROFILE_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS memory_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL,
        persona_id TEXT,
        profile_json TEXT NOT NULL DEFAULT '{}',
        pending_json TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(scope, persona_id)
    )
    """,
    # SQLite treats NULL values as distinct in a UNIQUE constraint, so the
    # expression index is required to keep "scope + no persona" single-row.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_scope_persona
    ON memory_profiles(scope, COALESCE(persona_id, ''))
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_profile_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL,
        version INTEGER NOT NULL,
        profile_json TEXT NOT NULL,
        changed_fields TEXT NOT NULL DEFAULT '[]',
        evidence_memory_ids TEXT NOT NULL DEFAULT '[]',
        source TEXT NOT NULL DEFAULT 'reflection',
        created_at REAL NOT NULL,
        FOREIGN KEY(profile_id) REFERENCES memory_profiles(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_history_profile
    ON memory_profile_history(profile_id, created_at DESC)
    """,
)

# External memory API schema: API keys, per-key idempotency records.
# Audit records live in a JSONL file next to the database, not in SQLite, so the
# table schema stays small and audit retention can be trimmed without VACUUM.
# All statements are idempotent and intentionally version-independent:
# ``MemoryEngine._create_tables`` executes them on every plugin start (fresh and
# upgraded databases), and ``ExternalApiStore.initialize`` repeats them for the
# standalone listener/tests. This keeps db_version reserved for other features.
EXTERNAL_API_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS external_api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        key_prefix TEXT NOT NULL,
        key_hash TEXT NOT NULL UNIQUE,
        scopes TEXT NOT NULL DEFAULT '["read"]',
        allowed_personas TEXT,
        allowed_scopes TEXT,
        rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
        write_limit_per_min INTEGER NOT NULL DEFAULT 20,
        created_at REAL NOT NULL,
        last_used_at REAL,
        revoked_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_api_idempotency (
        idempotency_key TEXT PRIMARY KEY,
        memory_id INTEGER NOT NULL,
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_external_api_keys_prefix ON external_api_keys(key_prefix)",
    "CREATE INDEX IF NOT EXISTS idx_external_api_idempotency_created ON external_api_idempotency(created_at)",
)

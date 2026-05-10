"""Memory and knowledge graph migrations (v16-v31): entities, scopes, anchors."""

_MIGRATIONS_MEMORY: list[tuple[int, str, str]] = [
    (
        16,
        "Change user_memories.importance from INTEGER to REAL for precise decay",
        """
        PRAGMA foreign_keys=OFF;

        CREATE TABLE IF NOT EXISTS user_memories_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            importance REAL NOT NULL DEFAULT 3.0,
            expires_at TIMESTAMP,
            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            access_count INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            embedding_id TEXT,
            session_id TEXT,
            turn_index INTEGER,
            superseded_by TEXT,
            relevance_score REAL DEFAULT 3.0,
            UNIQUE(user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_memories_user_new ON user_memories_new(user_id);

        INSERT INTO user_memories_new
            (id, user_id, key, value, source, created_at, updated_at,
             importance, expires_at, last_accessed, access_count, category,
             embedding_id, session_id, turn_index, superseded_by, relevance_score)
        SELECT
            id, user_id, key, value, source, created_at, updated_at,
            CAST(importance AS REAL), expires_at, last_accessed, access_count, category,
            embedding_id, session_id, turn_index, superseded_by, relevance_score
        FROM user_memories;

        DROP TABLE user_memories;
        ALTER TABLE user_memories_new RENAME TO user_memories;
        CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        17,
        "Add idempotency_keys table for API idempotency",
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            user_id TEXT NOT NULL,
            response_body TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at);
        """,
    ),
    (
        18,
        "Add segments_json column and speaker_mappings table",
        """
        ALTER TABLE meeting_files ADD COLUMN segments_json TEXT;

        CREATE TABLE IF NOT EXISTS speaker_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES meeting_files(id) ON DELETE CASCADE,
            meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            speaker_code TEXT NOT NULL,
            speaker_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_id, speaker_code)
        );
        CREATE INDEX IF NOT EXISTS idx_speaker_mappings_file ON speaker_mappings(file_id);
        CREATE INDEX IF NOT EXISTS idx_speaker_mappings_meeting ON speaker_mappings(meeting_id);
        """,
    ),
    (
        19,
        "Add sources_json column to chat_messages for source provenance",
        "ALTER TABLE chat_messages ADD COLUMN sources_json TEXT;",
    ),
    (
        20,
        "Add body hash to idempotency keys",
        """
        ALTER TABLE idempotency_keys ADD COLUMN body_hash TEXT;
        CREATE INDEX IF NOT EXISTS idx_idempotency_lookup
            ON idempotency_keys(method, path, user_id, body_hash, expires_at);
        """,
    ),
    (
        21,
        "Add per-meeting content hash uniqueness for files",
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY meeting_id, content_hash
                    ORDER BY
                        CASE status
                            WHEN 'ready' THEN 2
                            WHEN 'processing' THEN 1
                            ELSE 0
                        END DESC,
                        id DESC
                ) AS rn
            FROM meeting_files
            WHERE content_hash IS NOT NULL
        )
        DELETE FROM meeting_files
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_meeting_files_meeting_hash_unique
            ON meeting_files(meeting_id, content_hash)
            WHERE content_hash IS NOT NULL;
        """,
    ),
    (
        22,
        "Add typed file artefact columns on meeting_files",
        """
        ALTER TABLE meeting_files ADD COLUMN structured_json TEXT;
        ALTER TABLE meeting_files ADD COLUMN structured_kind TEXT;
        ALTER TABLE meeting_files ADD COLUMN summary TEXT;
        ALTER TABLE meeting_files ADD COLUMN key_points_json TEXT;
        ALTER TABLE meeting_files ADD COLUMN duration_seconds REAL;
        ALTER TABLE meeting_files ADD COLUMN page_count INTEGER;
        ALTER TABLE meeting_files ADD COLUMN word_count INTEGER;
        ALTER TABLE meeting_files ADD COLUMN language TEXT;

        UPDATE meeting_files
        SET structured_json = segments_json,
            structured_kind = 'segments'
        WHERE segments_json IS NOT NULL
          AND (structured_json IS NULL OR structured_json = '');
        """,
    ),
    (
        23,
        "Add pending_vector_deletions for orphan vector cleanup",
        """
        CREATE TABLE IF NOT EXISTS pending_vector_deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            embedding_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_pending_vec_collection
            ON pending_vector_deletions(collection);
        """,
    ),
    (
        24,
        "Add metrics_json to meeting_files artefacts",
        """
        ALTER TABLE meeting_files ADD COLUMN metrics_json TEXT;
        """,
    ),
    (
        25,
        "Add RAGAnything doc tracking columns on meeting_files",
        """
        ALTER TABLE meeting_files ADD COLUMN raganything_doc_id TEXT;
        ALTER TABLE meeting_files ADD COLUMN raganything_indexed_at TIMESTAMP;
        CREATE INDEX IF NOT EXISTS idx_meeting_files_raga_doc ON meeting_files(raganything_doc_id);
        """,
    ),
    (
        26,
        "Add index_state table for cross-index consistency",
        """
        CREATE TABLE IF NOT EXISTS index_state (
            file_id INTEGER PRIMARY KEY REFERENCES meeting_files(id) ON DELETE CASCADE,
            meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            chroma_indexed_at TIMESTAMP,
            raganything_indexed_at TIMESTAMP,
            raganything_doc_id TEXT,
            last_error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_index_state_meeting_id ON index_state(meeting_id);
        """,
    ),
    (
        27,
        "Add processing_started_at timestamps for stuck-processing recovery",
        """
        ALTER TABLE meetings ADD COLUMN processing_started_at TIMESTAMP;
        ALTER TABLE meeting_files ADD COLUMN processing_started_at TIMESTAMP;
        UPDATE meetings
        SET processing_started_at = updated_at
        WHERE status='processing' AND processing_started_at IS NULL;
        UPDATE meeting_files
        SET processing_started_at = updated_at
        WHERE status='processing' AND processing_started_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_meetings_processing_started
            ON meetings(processing_started_at);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_processing_started
            ON meeting_files(processing_started_at);
        """,
    ),
    (
        28,
        "Add scope (meeting_ids/file_ids) columns to user_memories and memory_entities",
        """
        ALTER TABLE user_memories ADD COLUMN meeting_ids TEXT;
        ALTER TABLE user_memories ADD COLUMN file_ids TEXT;
        ALTER TABLE memory_entities ADD COLUMN meeting_ids TEXT;
        ALTER TABLE memory_entities ADD COLUMN file_ids TEXT;
        """,
    ),
    (
        29,
        "Flag pre-scope memories/entities as legacy so they don't pollute scoped queries",
        """
        ALTER TABLE user_memories ADD COLUMN is_legacy_scope INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE memory_entities ADD COLUMN is_legacy_scope INTEGER NOT NULL DEFAULT 0;
        UPDATE user_memories
           SET is_legacy_scope = 1
         WHERE meeting_ids IS NULL AND file_ids IS NULL;
        UPDATE memory_entities
           SET is_legacy_scope = 1
         WHERE meeting_ids IS NULL AND file_ids IS NULL;
        """,
    ),
    (
        30,
        "Add conversational anchor columns to chat_sessions",
        """
        ALTER TABLE chat_sessions ADD COLUMN anchor_data TEXT;
        ALTER TABLE chat_sessions ADD COLUMN anchor_updated_at TIMESTAMP;
        """,
    ),
    (
        31,
        "Add aliases column to memory_entities for canonical-name merging",
        """
        ALTER TABLE memory_entities ADD COLUMN aliases TEXT;
        """,
    ),
]

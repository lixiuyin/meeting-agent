"""Core schema migrations (v1-v15): initial tables, sessions, BM25, FTS5."""

_MIGRATIONS_CORE: list[tuple[int, str, str]] = [
    (
        1,
        "Initial schema",
        """
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            file_type TEXT NOT NULL CHECK(file_type IN ('video', 'pdf', 'ppt', 'image')),
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'uploading'
                CHECK(status IN ('uploading', 'processing', 'ready', 'failed')),
            meeting_date TIMESTAMP,
            transcript TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON chat_sessions(user_id);

        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('system', 'human', 'ai')),
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);

        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id);
        """,
    ),
    (
        2,
        "Add error_message column to meetings",
        "ALTER TABLE meetings ADD COLUMN error_message TEXT;",
    ),
    (
        3,
        "Extend memories and sessions with importance, TTL, access tracking, and categories",
        """
        ALTER TABLE user_memories ADD COLUMN importance INTEGER NOT NULL DEFAULT 3;
        ALTER TABLE user_memories ADD COLUMN expires_at TIMESTAMP;
        ALTER TABLE user_memories ADD COLUMN last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        ALTER TABLE user_memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE user_memories ADD COLUMN category TEXT;
        ALTER TABLE user_memories ADD COLUMN embedding_id TEXT;
        ALTER TABLE chat_sessions ADD COLUMN last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        ALTER TABLE chat_sessions ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        4,
        "Add memory decay state tracking for auto-decay",
        """
        CREATE TABLE IF NOT EXISTS memory_decay_state (
            user_id TEXT PRIMARY KEY,
            last_decay_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        5,
        "Add content hash for upload idempotency",
        """
        ALTER TABLE meetings ADD COLUMN content_hash TEXT;
        CREATE INDEX IF NOT EXISTS idx_meetings_content_hash ON meetings(content_hash);
        """,
    ),
    (
        6,
        "Add BM25 index persistence tables",
        """
        CREATE TABLE IF NOT EXISTS bm25_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT NOT NULL UNIQUE,
            meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            tokenized TEXT NOT NULL,  -- JSON array of tokens
            metadata TEXT,  -- JSON object
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_bm25_meeting ON bm25_index(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_bm25_chunk ON bm25_index(chunk_id);

        CREATE TABLE IF NOT EXISTS bm25_stats (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL
        );
        INSERT OR IGNORE INTO bm25_stats (key, value) VALUES ('total_docs', 0);
        INSERT OR IGNORE INTO bm25_stats (key, value) VALUES ('avg_doc_len', 0);
        """,
    ),
    (
        7,
        "Add meeting_files table for multi-file support",
        """
        CREATE TABLE IF NOT EXISTS meeting_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            file_type TEXT NOT NULL CHECK(file_type IN (
                'video', 'audio', 'pdf', 'ppt', 'doc', 'xls', 'csv', 'txt', 'image'
            )),
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            content_hash TEXT,
            transcript TEXT,
            status TEXT DEFAULT 'processing'
                CHECK(status IN ('processing', 'summarizing', 'ready', 'error')),
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_meeting_files_meeting ON meeting_files(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_status ON meeting_files(status);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_hash ON meeting_files(content_hash);

        -- Migrate existing meetings data to meeting_files
        -- Map old status values to new constraint: uploading->processing, failed->error
        INSERT INTO meeting_files (
                meeting_id, file_type, file_name, file_path, content_hash,
                transcript, status, error_message, created_at, updated_at
            )
        SELECT
            id,
            file_type,
            file_name,
            file_path,
            content_hash,
            transcript,
            CASE
                WHEN status = 'failed' THEN 'error'
                WHEN status = 'uploading' THEN 'processing'
                ELSE status
            END,
            error_message,
            created_at,
            updated_at
        FROM meetings WHERE file_name IS NOT NULL;
        """,
    ),
    (
        8,
        "Add FTS5 virtual table for full-text search",
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS bm25_chunks USING fts5(
            content,
            metadata,
            meeting_id UNINDEXED,
            chunk_id UNINDEXED,
            content='bm25_index',
            content_rowid='id'
        );

        -- Triggers to keep FTS5 in sync with bm25_index
        CREATE TRIGGER IF NOT EXISTS bm25_chunks_ai AFTER INSERT ON bm25_index BEGIN
            INSERT INTO bm25_chunks(rowid, content, metadata, meeting_id, chunk_id)
            VALUES (new.id, new.content, new.metadata, new.meeting_id, new.chunk_id);
        END;

        CREATE TRIGGER IF NOT EXISTS bm25_chunks_ad AFTER DELETE ON bm25_index BEGIN
            INSERT INTO bm25_chunks(bm25_chunks, rowid, content, metadata, meeting_id, chunk_id)
            VALUES ('delete', old.id, old.content, old.metadata, old.meeting_id, old.chunk_id);
        END;

        CREATE TRIGGER IF NOT EXISTS bm25_chunks_au AFTER UPDATE ON bm25_index BEGIN
            INSERT INTO bm25_chunks(bm25_chunks, rowid, content, metadata, meeting_id, chunk_id)
            VALUES ('delete', old.id, old.content, old.metadata, old.meeting_id, old.chunk_id);
            INSERT INTO bm25_chunks(rowid, content, metadata, meeting_id, chunk_id)
            VALUES (new.id, new.content, new.metadata, new.meeting_id, new.chunk_id);
        END;
        """,
    ),
    (
        9,
        "Relax NOT NULL constraints on meetings file columns (moved to meeting_files)",
        """
        PRAGMA foreign_keys=OFF;

        CREATE TABLE IF NOT EXISTS meetings_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            file_type TEXT CHECK(file_type IN (
                'video', 'audio', 'pdf', 'ppt', 'doc', 'xls', 'csv', 'txt', 'image'
            )),
            file_name TEXT,
            file_path TEXT,
            status TEXT NOT NULL DEFAULT 'uploading'
                CHECK(status IN ('uploading', 'processing', 'ready', 'failed', 'error')),
            meeting_date TIMESTAMP,
            transcript TEXT,
            error_message TEXT,
            content_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO meetings_new
            (id, title, description, file_type, file_name, file_path, status,
             meeting_date, transcript, error_message, content_hash, created_at, updated_at)
        SELECT id, title, description, file_type, file_name, file_path, status,
               meeting_date, transcript, error_message, content_hash, created_at, updated_at
        FROM meetings;

        DROP TABLE meetings;
        ALTER TABLE meetings_new RENAME TO meetings;
        CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
        CREATE INDEX IF NOT EXISTS idx_meetings_content_hash ON meetings(content_hash);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        10,
        "Add session_summaries table for episodic cross-session memory",
        """
        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE REFERENCES chat_sessions(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            topics TEXT,
            key_entities TEXT,
            decisions TEXT,
            turn_count INTEGER,
            embedding_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_session_summaries_user ON session_summaries(user_id);
        """,
    ),
    (
        11,
        "Add FTS5 over chat_messages for cross-session full-text search",
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
            content,
            content='chat_messages',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS chat_messages_fts_ai AFTER INSERT ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(rowid, content) VALUES (new.id, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS chat_messages_fts_ad AFTER DELETE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END;
        """,
    ),
    (
        12,
        "Add source provenance columns to user_memories",
        """
        ALTER TABLE user_memories ADD COLUMN session_id TEXT;
        ALTER TABLE user_memories ADD COLUMN turn_index INTEGER;
        """,
    ),
    (
        13,
        "Add consolidation and float decay columns to user_memories",
        """
        ALTER TABLE user_memories ADD COLUMN superseded_by TEXT;
        ALTER TABLE user_memories ADD COLUMN relevance_score REAL DEFAULT 3.0;
        """,
    ),
    (
        14,
        "Add knowledge graph entity table",
        """
        CREATE TABLE IF NOT EXISTS memory_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            description TEXT,
            embedding_id TEXT,
            first_seen_session TEXT,
            last_seen_session TEXT,
            mention_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name, entity_type)
        );
        CREATE INDEX IF NOT EXISTS idx_entities_user ON memory_entities(user_id);
        CREATE INDEX IF NOT EXISTS idx_entities_type ON memory_entities(user_id, entity_type);
        """,
    ),
    (
        15,
        "Add knowledge graph relation table",
        """
        CREATE TABLE IF NOT EXISTS memory_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            subject_id INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            predicate TEXT NOT NULL,
            object_id INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            confidence REAL DEFAULT 1.0,
            source_session TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, subject_id, predicate, object_id)
        );
        CREATE INDEX IF NOT EXISTS idx_relations_subject ON memory_relations(subject_id);
        CREATE INDEX IF NOT EXISTS idx_relations_object ON memory_relations(object_id);
        """,
    ),
]

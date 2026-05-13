"""Feature migrations (v32-v47): summaries, multi-tenant, audit, indexes."""

_MIGRATIONS_FEATURES: list[tuple[int, str, str]] = [
    (
        32,
        "Migrate scope IDs from CSV columns to memory_scopes / entity_scopes junction tables",
        """
        CREATE TABLE IF NOT EXISTS memory_scopes (
            memory_id INTEGER NOT NULL REFERENCES user_memories(id) ON DELETE CASCADE,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('meeting', 'file')),
            scope_id INTEGER NOT NULL,
            PRIMARY KEY (memory_id, scope_type, scope_id)
        );

        CREATE INDEX IF NOT EXISTS idx_memory_scopes_lookup
            ON memory_scopes(scope_type, scope_id);

        CREATE TABLE IF NOT EXISTS entity_scopes (
            entity_id INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('meeting', 'file')),
            scope_id INTEGER NOT NULL,
            PRIMARY KEY (entity_id, scope_type, scope_id)
        );

        CREATE INDEX IF NOT EXISTS idx_entity_scopes_lookup
            ON entity_scopes(scope_type, scope_id);

        INSERT OR IGNORE INTO memory_scopes (memory_id, scope_type, scope_id)
        WITH RECURSIVE split(memory_id, val, rest) AS (
            SELECT id, NULL, meeting_ids || ','
            FROM user_memories
            WHERE meeting_ids IS NOT NULL AND meeting_ids != ''
            UNION ALL
            SELECT memory_id,
                   substr(rest, 1, instr(rest, ',') - 1),
                   substr(rest, instr(rest, ',') + 1)
            FROM split WHERE rest != ''
        )
        SELECT memory_id, 'meeting', CAST(trim(val) AS INTEGER)
        FROM split
        WHERE val IS NOT NULL AND trim(val) != '' AND trim(val) GLOB '[0-9]*';

        INSERT OR IGNORE INTO memory_scopes (memory_id, scope_type, scope_id)
        WITH RECURSIVE split(memory_id, val, rest) AS (
            SELECT id, NULL, file_ids || ','
            FROM user_memories
            WHERE file_ids IS NOT NULL AND file_ids != ''
            UNION ALL
            SELECT memory_id,
                   substr(rest, 1, instr(rest, ',') - 1),
                   substr(rest, instr(rest, ',') + 1)
            FROM split WHERE rest != ''
        )
        SELECT memory_id, 'file', CAST(trim(val) AS INTEGER)
        FROM split
        WHERE val IS NOT NULL AND trim(val) != '' AND trim(val) GLOB '[0-9]*';

        INSERT OR IGNORE INTO entity_scopes (entity_id, scope_type, scope_id)
        WITH RECURSIVE split(entity_id, val, rest) AS (
            SELECT id, NULL, meeting_ids || ','
            FROM memory_entities
            WHERE meeting_ids IS NOT NULL AND meeting_ids != ''
            UNION ALL
            SELECT entity_id,
                   substr(rest, 1, instr(rest, ',') - 1),
                   substr(rest, instr(rest, ',') + 1)
            FROM split WHERE rest != ''
        )
        SELECT entity_id, 'meeting', CAST(trim(val) AS INTEGER)
        FROM split
        WHERE val IS NOT NULL AND trim(val) != '' AND trim(val) GLOB '[0-9]*';

        INSERT OR IGNORE INTO entity_scopes (entity_id, scope_type, scope_id)
        WITH RECURSIVE split(entity_id, val, rest) AS (
            SELECT id, NULL, file_ids || ','
            FROM memory_entities
            WHERE file_ids IS NOT NULL AND file_ids != ''
            UNION ALL
            SELECT entity_id,
                   substr(rest, 1, instr(rest, ',') - 1),
                   substr(rest, instr(rest, ',') + 1)
            FROM split WHERE rest != ''
        )
        SELECT entity_id, 'file', CAST(trim(val) AS INTEGER)
        FROM split
        WHERE val IS NOT NULL AND trim(val) != '' AND trim(val) GLOB '[0-9]*';
        """,
    ),
    (
        33,
        "Add file-level summary FTS5 index for hybrid routing",
        """
        CREATE TABLE IF NOT EXISTS file_summary_bm25 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL UNIQUE,
            meeting_id INTEGER NOT NULL,
            summary TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_file_summary_bm25_meeting
            ON file_summary_bm25(meeting_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS file_summary_fts USING fts5(
            summary,
            content='file_summary_bm25',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS file_summary_fts_ai AFTER INSERT ON file_summary_bm25 BEGIN
            INSERT INTO file_summary_fts(rowid, summary) VALUES (new.id, new.summary);
        END;

        CREATE TRIGGER IF NOT EXISTS file_summary_fts_ad
            AFTER DELETE ON file_summary_bm25 BEGIN
            INSERT INTO file_summary_fts(file_summary_fts, rowid, summary)
                VALUES('delete', old.id, old.summary);
        END;

        CREATE TRIGGER IF NOT EXISTS file_summary_fts_au
            AFTER UPDATE ON file_summary_bm25 BEGIN
            INSERT INTO file_summary_fts(file_summary_fts, rowid, summary)
                VALUES('delete', old.id, old.summary);
            INSERT INTO file_summary_fts(rowid, summary)
                VALUES (new.id, new.summary);
        END;

        -- Backfill from existing summaries
        INSERT OR IGNORE INTO file_summary_bm25 (file_id, meeting_id, summary)
            SELECT id, meeting_id, summary
            FROM meeting_files
            WHERE summary IS NOT NULL AND summary != '';
        """,
    ),
    (
        34,
        "Add summary_status column to meetings table",
        """
        ALTER TABLE meetings ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(summary_status IN ('pending', 'ready', 'failed'));
        """,
    ),
    (
        35,
        "Add meeting_summaries table for meeting-level summaries",
        """
        CREATE TABLE IF NOT EXISTS meeting_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER NOT NULL UNIQUE REFERENCES meetings(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            contributing_file_ids TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_meeting_summaries_meeting
            ON meeting_summaries(meeting_id);
        """,
    ),
    (
        36,
        "Add summary_status column to meeting_files table",
        """
        ALTER TABLE meeting_files ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(summary_status IN ('pending', 'ready', 'failed'));
        """,
    ),
    (
        37,
        "Relax meetings.summary_status CHECK to include 'generating' + add lock_owner",
        """
        PRAGMA foreign_keys=OFF;

        ALTER TABLE meetings ADD COLUMN summary_lock_owner TEXT DEFAULT NULL;

        -- Recreate meetings with relaxed summary_status CHECK (SQLite has no ALTER COLUMN).
        -- All columns present before this migration must be listed to avoid data loss.
        CREATE TABLE meetings_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
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
            processing_started_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(summary_status IN ('pending', 'ready', 'failed', 'generating')),
            summary_lock_owner TEXT DEFAULT NULL
        );
        INSERT INTO meetings_new
            (id, title, description, file_type, file_name, file_path, status,
             meeting_date, transcript, error_message, content_hash, processing_started_at,
             created_at, updated_at, summary_status, summary_lock_owner)
            SELECT id, title, description, file_type, file_name, file_path, status,
                   meeting_date, transcript, error_message, content_hash, processing_started_at,
                   created_at, updated_at, summary_status, summary_lock_owner
            FROM meetings;
        DROP TABLE meetings;
        ALTER TABLE meetings_new RENAME TO meetings;
        CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
        CREATE INDEX IF NOT EXISTS idx_meetings_content_hash ON meetings(content_hash);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        38,
        "Relax meeting_files.status CHECK to include 'summarizing'",
        """
        PRAGMA foreign_keys=OFF;

        CREATE TABLE meeting_files_new (
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            segments_json TEXT,
            structured_json TEXT,
            structured_kind TEXT,
            summary TEXT,
            key_points_json TEXT,
            duration_seconds REAL,
            page_count INTEGER,
            word_count INTEGER,
            language TEXT,
            metrics_json TEXT,
            raganything_doc_id TEXT,
            raganything_indexed_at TIMESTAMP,
            processing_started_at TIMESTAMP,
            summary_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(summary_status IN ('pending', 'ready', 'failed'))
        );
        INSERT INTO meeting_files_new SELECT * FROM meeting_files;
        DROP TABLE meeting_files;
        ALTER TABLE meeting_files_new RENAME TO meeting_files;
        CREATE INDEX IF NOT EXISTS idx_meeting_files_meeting ON meeting_files(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_status ON meeting_files(status);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_hash ON meeting_files(content_hash);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_meeting_files_meeting_hash_unique
            ON meeting_files(meeting_id, content_hash)
            WHERE content_hash IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_meeting_files_raga_doc ON meeting_files(raganything_doc_id);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_processing_started
            ON meeting_files(processing_started_at);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        39,
        "Relax meeting_files.summary_status CHECK to include 'generating'",
        """
        PRAGMA foreign_keys=OFF;

        CREATE TABLE meeting_files_new (
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            segments_json TEXT,
            structured_json TEXT,
            structured_kind TEXT,
            summary TEXT,
            key_points_json TEXT,
            duration_seconds REAL,
            page_count INTEGER,
            word_count INTEGER,
            language TEXT,
            metrics_json TEXT,
            raganything_doc_id TEXT,
            raganything_indexed_at TIMESTAMP,
            processing_started_at TIMESTAMP,
            summary_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(summary_status IN ('pending', 'generating', 'ready', 'failed'))
        );
        INSERT INTO meeting_files_new SELECT * FROM meeting_files;
        DROP TABLE meeting_files;
        ALTER TABLE meeting_files_new RENAME TO meeting_files;
        CREATE INDEX IF NOT EXISTS idx_meeting_files_meeting ON meeting_files(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_status ON meeting_files(status);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_hash ON meeting_files(content_hash);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_meeting_files_meeting_hash_unique
            ON meeting_files(meeting_id, content_hash)
            WHERE content_hash IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_meeting_files_raga_doc ON meeting_files(raganything_doc_id);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_processing_started
            ON meeting_files(processing_started_at);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        40,
        "Relax meetings.status CHECK to include 'summarizing'",
        """
        PRAGMA foreign_keys=OFF;

        -- Recreate meetings with relaxed status CHECK (SQLite has no ALTER COLUMN).
        CREATE TABLE meetings_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            description TEXT,
            file_type TEXT CHECK(file_type IN (
                'video', 'audio', 'pdf', 'ppt', 'doc', 'xls', 'csv', 'txt', 'image'
            )),
            file_name TEXT,
            file_path TEXT,
            status TEXT NOT NULL DEFAULT 'uploading'
                CHECK(status IN (
                    'uploading', 'processing', 'summarizing', 'ready', 'failed', 'error'
                )),
            meeting_date TIMESTAMP,
            transcript TEXT,
            error_message TEXT,
            content_hash TEXT,
            processing_started_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(summary_status IN ('pending', 'ready', 'failed', 'generating')),
            summary_lock_owner TEXT DEFAULT NULL
        );
        INSERT INTO meetings_new
            (id, title, description, file_type, file_name, file_path, status,
             meeting_date, transcript, error_message, content_hash, processing_started_at,
             created_at, updated_at, summary_status, summary_lock_owner)
            SELECT id, title, description, file_type, file_name, file_path, status,
                   meeting_date, transcript, error_message, content_hash, processing_started_at,
                   created_at, updated_at, summary_status, summary_lock_owner
            FROM meetings;
        DROP TABLE meetings;
        ALTER TABLE meetings_new RENAME TO meetings;
        CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
        CREATE INDEX IF NOT EXISTS idx_meetings_content_hash ON meetings(content_hash);

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        41,
        "Add user_id columns to meetings and meeting_files for multi-tenant data isolation",
        """
        ALTER TABLE meetings ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
        ALTER TABLE meeting_files ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default';
        CREATE INDEX IF NOT EXISTS idx_meetings_user_id ON meetings(user_id);
        CREATE INDEX IF NOT EXISTS idx_meeting_files_user_id ON meeting_files(user_id);
        """,
    ),
    (
        42,
        "Add memory_audit_log table for tracking memory lifecycle events",
        """
        CREATE TABLE IF NOT EXISTS memory_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            memory_key TEXT NOT NULL,
            action TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            detail TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_memory_audit_user
            ON memory_audit_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_memory_audit_action
            ON memory_audit_log(action);
        CREATE INDEX IF NOT EXISTS idx_memory_audit_created
            ON memory_audit_log(created_at);
        """,
    ),
    (
        43,
        "Add vector_state column to user_memories for tracking vector sync status (CRITICAL-3)",
        """
        ALTER TABLE user_memories ADD COLUMN vector_state TEXT NOT NULL DEFAULT 'synced';
        """,
    ),
    (
        44,
        "Add attempts column to pending_vector_deletions for retry tracking",
        """
        ALTER TABLE pending_vector_deletions ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        45,
        "Add composite index on chat_messages(session_id, id) for efficient DESC pagination",
        """
        CREATE INDEX IF NOT EXISTS idx_messages_session_id_desc
            ON chat_messages(session_id, id DESC);
        """,
    ),
    (
        46,
        "Add FK constraints to file_summary_bm25 for cascade deletion",
        """
        PRAGMA foreign_keys=OFF;

        CREATE TABLE file_summary_bm25_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL UNIQUE REFERENCES meeting_files(id) ON DELETE CASCADE,
            meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            summary TEXT NOT NULL DEFAULT ''
        );

        INSERT INTO file_summary_bm25_new SELECT * FROM file_summary_bm25;
        DROP TABLE file_summary_bm25;
        ALTER TABLE file_summary_bm25_new RENAME TO file_summary_bm25;

        CREATE INDEX IF NOT EXISTS idx_file_summary_bm25_meeting
            ON file_summary_bm25(meeting_id);

        DROP TABLE IF EXISTS file_summary_fts;
        CREATE VIRTUAL TABLE IF NOT EXISTS file_summary_fts USING fts5(
            summary,
            content='file_summary_bm25',
            content_rowid='id'
        );
        INSERT INTO file_summary_fts(rowid, summary)
            SELECT id, summary FROM file_summary_bm25;

        CREATE TRIGGER IF NOT EXISTS file_summary_fts_ai AFTER INSERT ON file_summary_bm25 BEGIN
            INSERT INTO file_summary_fts(rowid, summary) VALUES (new.id, new.summary);
        END;
        CREATE TRIGGER IF NOT EXISTS file_summary_fts_ad
            AFTER DELETE ON file_summary_bm25 BEGIN
            INSERT INTO file_summary_fts(file_summary_fts, rowid, summary)
                VALUES('delete', old.id, old.summary);
        END;
        CREATE TRIGGER IF NOT EXISTS file_summary_fts_au
            AFTER UPDATE ON file_summary_bm25 BEGIN
            INSERT INTO file_summary_fts(file_summary_fts, rowid, summary)
                VALUES('delete', old.id, old.summary);
            INSERT INTO file_summary_fts(rowid, summary) VALUES (new.id, new.summary);
        END;

        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        47,
        "Add expires_at to memory_audit_log and composite desc indexes for sessions/memories",
        """
        ALTER TABLE memory_audit_log ADD COLUMN expires_at TEXT;
        CREATE INDEX IF NOT EXISTS idx_memory_audit_expires
            ON memory_audit_log(expires_at)
            WHERE expires_at IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated_desc
            ON chat_sessions(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_user_memories_user_updated_desc
            ON user_memories(user_id, updated_at DESC);
        """,
    ),
    (
        48,
        "Add kv_state key-value table for cross-worker coordination (breaker, advisory locks)",
        """
        CREATE TABLE IF NOT EXISTS kv_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
]

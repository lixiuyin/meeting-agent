"""SQLite database initialization and CRUD operations

Uses a thread-local connection pool with WAL mode for concurrent reads
and a write lock to serialize mutations. This avoids 'database is locked'
errors under concurrent async access.

Schema changes are tracked via a lightweight migration system: each migration
is a (version, description, sql) tuple applied sequentially on startup.
"""

# Connection pool infrastructure
from ._connection import (
    _get_thread_conn,
    _write_lock,
    close_all_connections,
    get_connection,
    get_write_connection,
)

# Schema SQL + migrations + init_db
from ._migrations import (
    _MIGRATIONS,
    SCHEMA_SQL,
    init_db,
)

# Memory / entity scope junction CRUD
from ._scopes import (
    add_scopes,
    clear_scopes,
    get_scopes,
)

# BM25 index persistence
from .bm25 import (
    add_bm25_chunk,
    clear_bm25_index,
    delete_bm25_chunks_by_file,
    delete_bm25_chunks_by_meeting,
    delete_file_summaries_bm25_by_meeting,
    delete_file_summary_bm25,
    fts5_search,
    fts5_search_file_summaries,
    get_all_bm25_chunks,
    get_bm25_stats,
    get_page_sibling_chunks,
    update_bm25_stats,
    upsert_file_summary_bm25,
)

# Session/Message/Summary/FTS5 CRUD
from .chat import (
    add_message,
    backfill_chat_messages_fts,
    clear_messages,
    count_messages,
    count_session_summaries,
    count_sessions,
    create_session,
    delete_session,
    delete_session_summary,
    ensure_session,
    get_messages,
    get_session,
    get_session_summaries_batch,
    get_session_summary,
    get_unsummarized_sessions,
    list_session_summaries,
    list_sessions,
    read_anchor,
    search_chat_messages,
    touch_anchor,
    touch_session,
    upsert_session_summary,
    write_anchor,
)

# Idempotency CRUD
from .idempotency import (
    cleanup_expired_idempotency_keys,
    get_idempotency_response,
    save_idempotency_response,
)

# Index consistency state CRUD
from .index_state import (
    clear_index_state,
    mark_chroma_indexed,
    mark_raganything_failed,
    mark_raganything_indexed,
    reconcile_index_state,
)

# Knowledge graph entity + relation CRUD
from .knowledge_graph import (
    add_entity_aliases,
    delete_entity,
    get_entity_by_id,
    get_entity_by_name,
    list_entities,
    list_entity_relations,
    reassign_entity_relations,
    upsert_entity,
    upsert_relation,
)

# Meeting CRUD + Meeting files CRUD
from .meetings import (
    bulk_upsert_speaker_mappings,
    clear_file_summary,
    clear_meeting_summary,
    count_meeting_files,
    count_meeting_files_by_status,
    count_meetings,
    create_meeting,
    create_meeting_file,
    create_meeting_file_if_absent,
    delete_meeting,
    delete_meeting_file,
    delete_speaker_mappings,
    get_file_ids_for_speakers,
    get_file_metadata_bulk,
    get_meeting,
    get_meeting_file,
    get_meeting_file_by_hash,
    get_meeting_file_by_raganything_doc_id,
    get_meeting_file_status_counts,
    get_meeting_files_summaries,
    get_meeting_summary,
    get_meeting_summary_with_status,
    get_meeting_transcripts,
    get_segments_json,
    list_distinct_file_types_bulk,
    list_meeting_files,
    list_meetings,
    list_ready_file_ids_for_meetings,
    list_ready_meeting_files,
    list_recent_ready_file_ids,
    list_speaker_mappings,
    save_meeting_summary,
    save_segments_json,
    update_file_summary_status,
    update_meeting,
    update_meeting_file_artefact,
    update_meeting_file_raganything,
    update_meeting_file_status,
    update_meeting_file_summary,
    update_meeting_status,
    update_meeting_summary_status,
    upsert_speaker_mapping,
)

# User memory CRUD
from .memories import (
    cleanup_expired_audit_logs,
    count_memories,
    delete_expired_memories,
    delete_memory,
    get_expired_memory_ids,
    get_memories_batch,
    get_memories_for_consolidation,
    get_memory,
    get_memory_full,
    get_memory_timeline,
    list_and_count_memories,
    list_memories,
    mark_memory_superseded,
    search_memories_by_importance,
    set_memory,
    touch_memory_access,
    update_memory,
    update_memory_importance,
    update_memory_relevance_score,
)

__all__ = [
    # Migrations
    "SCHEMA_SQL",
    "_MIGRATIONS",
    "_get_thread_conn",
    "_write_lock",
    # BM25
    "add_bm25_chunk",
    # Knowledge graph
    "add_entity_aliases",
    # Chat / Sessions / Messages
    "add_message",
    # Scope junction tables
    "add_scopes",
    "backfill_chat_messages_fts",
    # Meetings
    "bulk_upsert_speaker_mappings",
    "cleanup_expired_audit_logs",
    "cleanup_expired_idempotency_keys",
    "clear_bm25_index",
    "clear_file_summary",
    "clear_index_state",
    "clear_meeting_summary",
    "clear_messages",
    "clear_scopes",
    # Connection
    "close_all_connections",
    "count_meeting_files",
    "count_meeting_files_by_status",
    "count_meetings",
    # Memories
    "count_memories",
    "count_messages",
    "count_session_summaries",
    "count_sessions",
    "create_meeting",
    "create_meeting_file",
    "create_meeting_file_if_absent",
    "create_session",
    "delete_bm25_chunks_by_file",
    "delete_bm25_chunks_by_meeting",
    # Knowledge graph
    "delete_entity",
    "delete_expired_memories",
    "delete_file_summaries_bm25_by_meeting",
    # File summary BM25
    "delete_file_summary_bm25",
    "delete_meeting",
    "delete_meeting_file",
    "delete_memory",
    "delete_session",
    "delete_session_summary",
    "delete_speaker_mappings",
    "ensure_session",
    "fts5_search",
    "fts5_search_file_summaries",
    "get_all_bm25_chunks",
    "get_bm25_stats",
    "get_connection",
    "get_entity_by_id",
    "get_entity_by_name",
    "get_expired_memory_ids",
    "get_file_ids_for_speakers",
    "get_file_metadata_bulk",
    # Idempotency
    "get_idempotency_response",
    "get_meeting",
    "get_meeting_file",
    "get_meeting_file_by_hash",
    "get_meeting_file_by_raganything_doc_id",
    "get_meeting_file_status_counts",
    "get_meeting_files_summaries",
    "get_meeting_summary",
    "get_meeting_summary_with_status",
    "get_meeting_transcripts",
    "get_memories_batch",
    "get_memories_for_consolidation",
    "get_memory",
    "get_memory_full",
    "get_memory_timeline",
    "get_messages",
    "get_page_sibling_chunks",
    "get_scopes",
    "get_segments_json",
    "get_session",
    "get_session_summaries_batch",
    "get_session_summary",
    "get_unsummarized_sessions",
    "get_write_connection",
    "init_db",
    "list_and_count_memories",
    "list_distinct_file_types_bulk",
    "list_entities",
    "list_entity_relations",
    "list_meeting_files",
    "list_meetings",
    "list_memories",
    "list_ready_file_ids_for_meetings",
    "list_ready_meeting_files",
    "list_recent_ready_file_ids",
    "list_session_summaries",
    "list_sessions",
    "list_speaker_mappings",
    "mark_chroma_indexed",
    "mark_memory_superseded",
    "mark_raganything_failed",
    "mark_raganything_indexed",
    "read_anchor",
    "reassign_entity_relations",
    "reconcile_index_state",
    "save_idempotency_response",
    "save_meeting_summary",
    "save_segments_json",
    "search_chat_messages",
    "search_memories_by_importance",
    "set_memory",
    "touch_anchor",
    "touch_memory_access",
    "touch_session",
    "update_bm25_stats",
    "update_file_summary_status",
    "update_meeting",
    "update_meeting_file_artefact",
    "update_meeting_file_raganything",
    "update_meeting_file_status",
    "update_meeting_file_summary",
    "update_meeting_status",
    "update_meeting_summary_status",
    "update_memory",
    "update_memory_importance",
    "update_memory_relevance_score",
    "upsert_entity",
    "upsert_file_summary_bm25",
    "upsert_relation",
    "upsert_session_summary",
    "upsert_speaker_mapping",
    "write_anchor",
]

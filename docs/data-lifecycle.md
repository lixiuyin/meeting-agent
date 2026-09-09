# Data lifecycle and storage model

Meeting Agent has one durable source-of-truth layer and several derived
indexes. Understanding that boundary makes deletion, backup, reindexing, and
privacy reviews safer.

## Lifecycle overview

```text
client
  │ upload
  ▼
SQLite: meeting + file metadata, status, transcript, segments, summaries
  │                         │
  │                         ├── session messages and summaries
  │                         ├── long-term memories
  │                         └── knowledge-graph entities/relations
  │
  ├── original files and page/image assets → data/uploads/
  ├── chunk embeddings → data/vectordb/
  └── lexical index / BM25 metadata → SQLite FTS5 and supporting files
```

The ingest pipeline moves a file through validation, parsing or ASR, optional
vision/OCR, normalization, chunking, embedding, and index persistence. A file
should be treated as queryable only after its status is `ready` and the health
or consistency checks do not report a missing index.

## Storage responsibilities

| Store                      | Contains                                                                                                      |                                                                     Rebuildable? | Backup guidance                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------: | --------------------------------------------- |
| `data/meetings.db`         | Meetings, file metadata, normalized transcript, summaries, sessions, memories, KG, settings/idempotency state |                                                    No, except from a full backup | Back up transactionally                       |
| `data/uploads/`            | Original files and derived page/image assets                                                                  | Usually no for originals; assets may be regenerated only when the source permits | Back up with the database                     |
| `data/vectordb/`           | Chroma vector collections and embeddings                                                                      |       Yes, from ready transcripts; multimodal indexes may need their own rebuild | Back up for faster recovery                   |
| SQLite FTS5/BM25 state     | Lexical retrieval index and metadata                                                                          |                  Yes, from Chroma/database according to the RAG maintenance path | Include DB; rebuild if inconsistent           |
| `.env`                     | Secrets and deployment-specific overrides                                                                     |                                                                               No | Store in a secret manager or encrypted backup |
| `backend/config/main.yaml` | Non-secret defaults                                                                                           |                                                        Yes, from version control | Version with the release                      |

The exact paths can be overridden by `DB_PATH`, `UPLOAD_DIR`, and
`VECTOR_DB_DIR`. See [`backend/docs/configuration.md`](../backend/docs/configuration.md).

## Ownership and authentication

With an empty `API_KEY`, the service is deliberately shared development mode.
With `API_KEY` configured, the authenticated key maps to an HMAC-derived
principal and meeting, file, session, and memory access is ownership-filtered.
Production also requires `PRINCIPAL_PEPPER`. Operators may set `PRINCIPAL_ID`
to a verified existing database owner when rotating credentials; this preserves
one deployment identity but does not create multiple users or roles.

Do not interpret a local development database as a multi-tenant boundary. Use
separate deployments or configured API keys for isolation, and review
[`SECURITY.md`](../SECURITY.md) before exposing the service.

## Recall archival

Capacity, low-importance maintenance and recall expiry set `archived_at` and queue vector cleanup. Fact rows and historical versions remain stored; archival does not retract a business fact. Exact recorded-task queries include business-valid capacity-archived facts. Explicit deletion and account erasure retain their separate semantics. Generated profiles require current source revisions and admissible evidence.

See the [meeting workflow implementation and validation matrix](validation/meeting-workflow-2026-09-07.json)
for the point-in-time migration and validation boundaries captured on
2026-09-07.

## Deletion semantics

### Chat cancellation and branches

- **Stop generating** cancels the durable chat run. If visible partial text already exists, the user/assistant pair is saved with `degradation_reason=cancelled`; automatic fact extraction is skipped for that degraded turn.
- **Withdraw this message** cancels the run and switches the active UI to a new session branch that excludes the withdrawn turn. The original session remains available for audit and recovery.
- **Edit** and **Retry** never rewrite an existing message in place. They create a new session with `parent_session_id`, `branched_from_message_id`, and `branch_reason`, copy only the stable prefix before the selected user message, and generate the replacement in that branch.
- A branch copies raw prior messages and source citations, but does not copy a possibly stale session summary, task-state snapshot, or derived long-term memories. Those are recomputed or referenced through their original provenance as normal processing continues.

This design keeps the active conversation understandable without silently changing evidence that may already have influenced later answers. Arbitrary single-message hard deletion remains intentionally unsupported; use branch editing or the authenticated whole-session deletion path instead.

- Deleting a meeting removes its files and associated meeting-scoped data through the API path; verify the response and storage cleanup before treating the operation as complete.
- Deleting a single file removes that file from the meeting and should remove its derived chunks/assets according to the processor cleanup path.
- Deleting a session removes its messages and session summary; it does not imply deletion of long-term memories extracted from the conversation.
- Deleting or editing a memory is independent of the source meeting and session.
- Removing a source file does not make an external backup disappear. Apply retention and backup lifecycle policies separately.

For an account-level wipe, use the authenticated settings account endpoint only
after confirming the principal and backup requirements. It is intentionally a
destructive operation.

## Reprocessing versus vector rebuild

These operations have different cost and data effects:

| Operation | Re-runs parser/ASR/vision? | Reuses transcript? | Use when |
|---|---:|---:|---|
| File/meeting `reprocess` | Yes | No | Source extraction or speaker/asset data changed |
| `settings/rebuild-vectors` | No | Yes | Chunking, embedding, or retrieval configuration changed |
| Multimodal rebuild | No ASR; rebuilds optional multimodal index | Yes | RAGAnything/multimodal index is missing or stale |

A vector rebuild uses a shadow collection and swaps it only after a successful
build, so a failed rebuild should leave the live collection usable. A full
reprocess can call external providers and incur cost; plan retries accordingly.

## Retention and privacy checklist

Before production use, decide:

1. How long originals, transcripts, chat messages, memories, and summaries are retained.
2. Whether provider APIs may receive the uploaded content or prompts.
3. Who can access exported files and signed download URLs.
4. How API keys, parser credentials, and backups are rotated and encrypted.
5. How a user requests deletion and how long deletion takes to propagate to backups and derived indexes.
6. Whether logs and traces may contain filenames, query text, or provider error details.

The configured chat-message and decay-state retention jobs are documented in
[`backend/docs/operations/retention.md`](../backend/docs/operations/retention.md).
Retention is not a substitute for a legal or organizational data policy.

Account erasure is tracked as a durable batch. A `202` response means primary
SQLite data has been erased and external cleanup has been scheduled; clients
must poll the returned batch until it reaches `completed`. Dead-letter jobs are
observable and can be explicitly retried after the external store recovers.

## Backup and recovery boundary

For a consistent recovery point, back up the SQLite database and original
uploads together. Chroma can be rebuilt, but rebuilding costs time and may
require the current embedding provider. Follow:

- [`backend/docs/operations/backup.md`](../backend/docs/operations/backup.md)
- [`backend/docs/operations/restore.md`](../backend/docs/operations/restore.md)
- [`operations-guide.md`](operations-guide.md)

After restore, verify health, meeting visibility, transcript access, vector
consistency, a small retrieval query, and a new upload before reopening the
service to users.

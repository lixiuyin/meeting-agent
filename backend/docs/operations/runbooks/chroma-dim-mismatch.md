# Runbook: Chroma Dimension Mismatch

## Symptom

- retrieval/indexing fails with vector dimension mismatch errors
- errors appear after embedding model or dimension config changes

## Immediate Checks

1. Compare `EMBEDDING_DIMENSION` with actual embedding output size.
2. Check whether existing Chroma collection was created with an older dimension.
3. Verify current settings from `GET /api/v1/settings`.

## Mitigation Steps

1. Stop ingestion jobs temporarily.
2. Trigger vector rebuild endpoint:
   - `POST /api/v1/settings/rebuild-vectors`
3. If rebuild cannot proceed, snapshot data and recreate vector collection.

## Recovery Validation

- no new dimension mismatch errors in logs
- retrieval returns sources with normal score distribution
- ingest pipeline completes to `ready` status

## Prevention

- couple embedding model updates with mandatory vector rebuild in release checklist

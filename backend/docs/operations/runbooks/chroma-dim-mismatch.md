# Runbook: Chroma Dimension Mismatch

## Symptom

- retrieval/indexing fails with vector dimension mismatch errors
- errors appear after embedding model or dimension config changes

## Immediate Checks

1. Compare `EMBEDDING_DIMENSION` with actual embedding output size.
2. Check whether existing Chroma collection was created with an older dimension.
3. Verify current settings from `GET /api/v1/settings`.

## Mitigation Steps

1. Stop ingestion jobs and take a complete application-data backup.
2. Correct the embedding binding/model/dimension in deployment configuration; do not use the live settings endpoint, which rejects index-shaping changes.
3. Restart one backend instance. Startup reconciliation compares the active fingerprint with real Chroma/BM25 generations and queues durable file reprocessing.
4. Use `POST /api/v1/settings/rebuild-vectors` only for a compatible guarded refresh. It fails closed if stored source data cannot safely reconstruct every ready file.
5. Do not delete the live collection manually unless the backup has been verified and the manifest-driven repair path cannot recover it.

## Recovery Validation

- no new dimension mismatch errors in logs
- readiness reports zero `repair_pending_indexes` and zero `config_manifest_mismatches`
- retrieval returns sources with normal score distribution
- ingest pipeline completes to `ready` status

## Prevention

- couple embedding model updates with controlled restart, manifest reconciliation, and durable reprocessing in the release checklist

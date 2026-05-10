# Runbook: AssemblyAI Timeout or Slow Transcription

## Symptom

- ingest tasks stay in `processing` longer than expected
- transcription requests timeout or fail intermittently

## Immediate Checks

1. Validate AssemblyAI API key and account quota.
2. Check outbound network stability from backend runtime.
3. Review recent ingest queue depth and file size profile.

## Mitigation Steps

1. Retry failed transcriptions using existing reprocess endpoint.
2. Reduce concurrent ingest load temporarily.
3. Confirm ffmpeg extraction step is healthy and not the real bottleneck.

## Recovery Validation

- new uploads complete transcription within expected p95
- timeout/error rate drops to baseline
- no accumulation of stale `processing` meetings

## Escalation

- Escalate to provider support if timeout rate remains elevated for > 30 minutes.

# Service Level Agreement (SLA) Policy

This project is currently operated under an internal engineering SLA policy
for release readiness and incident response.

## Current SLA Level

- **External commercial SLA**: Not yet committed
- **Internal target SLA**: 99.5% monthly availability for critical API paths
- **Coverage window**: UTC calendar month

## Critical API Paths

- `POST /api/v1/chat`
- `POST /api/v1/chat/stream`
- `POST /api/v1/meetings/upload`
- `GET /api/v1/health/ready`

## Incident Response Targets

- **P1 (service largely unavailable)**: acknowledge within 15 minutes
- **P2 (degraded but functional)**: acknowledge within 60 minutes
- **P3 (minor/non-blocking)**: acknowledge within 1 business day

## Change Management Notes

- Any SLA target adjustment requires:
  - SLO metric impact review
  - runbook review
  - release note entry

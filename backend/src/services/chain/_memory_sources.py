"""Expose grounded memory provenance without changing RAG citation numbering."""

import json

from ...core import database as db
from ...core.source_revision_fence import meeting_file_source_token, meeting_file_source_tokens
from ...models.schemas.evidence import EvidenceLocationRequest
from ..evidence_location import resolve_evidence_location


def memory_evidence_sources(entries: list[dict], user_id: str) -> list[dict]:
    sources = []
    seen = set()
    files = {}
    with db.get_connection() as conn:
        for entry in entries:
            refs = entry.get("evidence_refs") or []
            if isinstance(refs, str):
                try:
                    refs = json.loads(refs)
                except (ValueError, TypeError):
                    continue
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if not isinstance(ref, dict) or type(ref.get("file_id")) is not int:
                    continue
                file_id = ref["file_id"]
                if file_id not in files:
                    files[file_id] = db.get_meeting_file(conn, file_id, user_id=user_id)
                file = files[file_id]
                if not file or file.get("approval_status") == "rejected":
                    continue
                if ref.get("meeting_id") not in (None, file["meeting_id"]):
                    continue
                revision = ref.get("source_revision")
                accepted = meeting_file_source_tokens(file)
                if revision and str(revision) not in accepted:
                    continue
                excerpt = entry.get("evidence_excerpt")
                if not excerpt:
                    continue
                try:
                    location = resolve_evidence_location(
                        file.get("transcript") or "",
                        {"kind": "text"},
                        EvidenceLocationRequest(
                            excerpt=excerpt,
                            window_start=ref.get("window_start"),
                            window_end=ref.get("window_end"),
                        ),
                    )
                except ValueError:
                    continue
                if location["status"] != "exact":
                    continue
                identity = (file_id, location["window_start"], location["window_end"])
                if identity in seen:
                    continue
                seen.add(identity)
                sources.append(
                    {
                        "meeting_id": file["meeting_id"],
                        "meeting_title": f"Meeting#{file['meeting_id']}",
                        "file_id": file_id,
                        "file_name": file["file_name"],
                        "file_type": file["file_type"],
                        "content": location["excerpt"],
                        "score": 0.0,
                        "source_kind": "text",
                        "document_revision": str(revision or meeting_file_source_token(file)),
                        "memory_key": entry["key"],
                        "window_start": location["window_start"],
                        "window_end": location["window_end"],
                        "evidence_excerpt": location["excerpt"],
                    }
                )
                if len(sources) >= 20:
                    return sources
    return sources

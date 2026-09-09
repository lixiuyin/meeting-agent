"""Authenticated, version-fenced per-file evidence resolution."""

import asyncio
import hashlib

from fastapi import Depends, HTTPException

from ....core import database as db
from ....core.security import verify_api_key
from ....models.schemas.evidence import EvidenceLocationRequest, EvidenceLocationResponse
from ....services.evidence_location import evidence_identity, resolve_evidence_location
from ._common import _build_meeting_file_response, _ownership_filter, router
from ._files import get_file_timeline


@router.post(
    "/{meeting_id}/files/{file_id}/evidence-location", response_model=EvidenceLocationResponse
)
async def locate_file_evidence(
    meeting_id: int,
    file_id: int,
    payload: EvidenceLocationRequest,
    principal: dict = Depends(verify_api_key),
):
    ownership = _ownership_filter(principal)

    def read_file():
        with db.get_connection() as conn:
            file = db.get_meeting_file(conn, file_id, user_id=ownership)
            if not file or file["meeting_id"] != meeting_id:
                raise HTTPException(404, "Source file not found")
            return file

    file = await asyncio.to_thread(read_file)
    revisions = _build_meeting_file_response(file).source_revisions
    source = file.get("transcript") or ""
    parser_revision = hashlib.sha256(source.encode()).hexdigest()
    revision = payload.source_revision or str(file.get("content_hash") or parser_revision)
    base = {
        "meeting_id": meeting_id,
        "file_id": file_id,
        "source_revision": revision,
        "parser_revision": parser_revision,
    }
    if payload.source_revision and payload.source_revision not in revisions:
        return EvidenceLocationResponse(status="version_changed", reason="source_replaced", **base)
    timeline = await get_file_timeline(meeting_id, file_id, principal)
    current = await asyncio.to_thread(read_file)
    if any(
        current.get(key) != file.get(key)
        for key in (
            "source_revision",
            "transcript",
            "content_hash",
            "active_index_generation",
            "approval_status",
        )
    ):
        return EvidenceLocationResponse(
            status="version_changed", reason="source_changed_during_resolution", **base
        )
    location = resolve_evidence_location(source, timeline.model_dump(), payload)
    if location["status"] in ("exact", "page_only"):
        location["evidence_id"] = evidence_identity(
            meeting_id, file_id, revision, parser_revision, location
        )
    return EvidenceLocationResponse(**base, **location)

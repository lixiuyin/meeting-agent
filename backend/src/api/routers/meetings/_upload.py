import asyncio
import contextlib
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile

from ....api.dependencies import IdempotencyGuard, idempotency_key_header
from ....api.middleware import limiter
from ....core import database as db
from ....core.audit import audit_log
from ....core.config import settings
from ....core.database import get_write_connection
from ....core.security import verify_api_key
from ....models.schemas import UploadResponse
from ._common import FILE_TYPE_MAP, _sanitize_filename, _validate_file_content, router


@router.post("/upload", response_model=UploadResponse)
@limiter.limit("20/minute")
async def upload_meeting(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(None),
    description: str = Form(None),
    meeting_id: int = Form(None),
    business_domain: Literal["unspecified", "meeting", "course", "research"] = Form("unspecified"),
    material_role: Literal["transcript", "minutes", "agenda", "decision_log", "attachment"]
    | None = Form(None),
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Upload a file to a meeting (create new or add to existing).

    - If meeting_id is provided: adds file to existing meeting
    - If meeting_id is None: creates new meeting with title/description

    Idempotency: if the exact same file content (SHA-256 hash) has already been
    uploaded and is ready, returns the existing file record.
    """
    from ....core.metrics import MEETING_UPLOAD_TOTAL

    _upload_status = "success"
    tmp_upload_path: Path | None = None
    save_path: Path | None = None
    file_record_id: int | None = None
    created_meeting_id: int | None = None
    completed = False
    try:
        # Validate extension
        raw_filename = file.filename or "unknown"
        suffix = Path(raw_filename).suffix.lower()
        if suffix not in FILE_TYPE_MAP:
            raise HTTPException(400, "Unsupported file format")

        file_type = FILE_TYPE_MAP[suffix]

        # Sanitize filename to prevent path traversal
        safe_name = _sanitize_filename(raw_filename)

        # Validate the target before accepting a potentially large body.  New
        # meetings are created only after the body has passed validation so a
        # failed upload cannot leave an empty meeting behind.
        if meeting_id:

            def _get_meeting():
                with db.get_connection() as conn:
                    return db.get_meeting(conn, meeting_id, user_id=principal["user_id"])

            meeting = await asyncio.to_thread(_get_meeting)
            if not meeting:
                raise HTTPException(404, "Meeting not found or access denied")
        elif not title:
            raise HTTPException(400, "Title is required for new meeting")

        # Read first chunk for magic bytes validation (before writing).
        # Malformed multipart bodies (mismatched boundary, empty payloads) can
        # leave the stream in a "consumed" state where read/seek raises
        # RuntimeError. Treat that as a client error rather than a 500.
        try:
            first_chunk = await file.read(4096)
            await file.seek(0)  # Reset to start for full read
        except RuntimeError as exc:
            raise HTTPException(
                400, f"Malformed upload: could not read file stream ({exc})"
            ) from exc

        # Validate magic bytes match the expected file type
        try:
            _validate_file_content(first_chunk, suffix)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        # Pre-check Content-Length header before streaming begins (H15)
        max_bytes = settings.MAX_UPLOAD_BYTES
        content_length_header = request.headers.get("content-length")
        if content_length_header is not None:
            try:
                content_length = int(content_length_header)
                if content_length > max_bytes:
                    raise HTTPException(
                        413,
                        f"File too large. Maximum size: {settings.MAX_UPLOAD_SIZE_MB}MB",
                    )
            except ValueError:
                pass  # Malformed Content-Length; rely on streaming check instead

        # Stream-read all content while computing SHA-256 hash with size limit
        hasher = hashlib.sha256()
        total = 0
        # M-17: Full UUID4 (128-bit) to prevent collision under concurrent uploads.
        incoming_path = settings.UPLOAD_DIR / f".upload-{uuid.uuid4().hex}"
        tmp_upload_path = incoming_path
        _UPLOAD_BYTE_TIMEOUT_SECONDS = max(60, int(max_bytes / (256 * 1024)))

        async def _stream_to_disk() -> None:
            nonlocal total
            fd = await asyncio.to_thread(
                os.open,
                incoming_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            tmp_f = os.fdopen(fd, "wb")
            try:
                while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                    hasher.update(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(
                            413,
                            f"File too large. Maximum size: {settings.MAX_UPLOAD_SIZE_MB}MB",
                        )
                    await asyncio.to_thread(tmp_f.write, chunk)
                await asyncio.to_thread(tmp_f.flush)
                await asyncio.to_thread(os.fsync, tmp_f.fileno())
            finally:
                await asyncio.to_thread(tmp_f.close)

        try:
            await asyncio.wait_for(_stream_to_disk(), timeout=_UPLOAD_BYTE_TIMEOUT_SECONDS)
        except TimeoutError:
            incoming_path.unlink(missing_ok=True)
            raise HTTPException(408, "Upload timed out") from None
        except asyncio.CancelledError:
            incoming_path.unlink(missing_ok=True)
            raise
        except HTTPException:
            # H15: clean up temp file on size-limit rejection during streaming
            incoming_path.unlink(missing_ok=True)
            raise
        except Exception:
            incoming_path.unlink(missing_ok=True)
            raise
        content_hash = hasher.hexdigest()

        # The multipart stream has already been consumed, so use a canonical
        # semantic fingerprint instead of re-reading ``request.body()``.
        fingerprint_payload = {
            "content_hash": content_hash,
            "description": description or "",
            "filename": safe_name,
            "meeting_id": meeting_id,
            "title": title or "",
            "business_domain": business_domain,
            "material_role": material_role,
        }
        upload_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        guard = IdempotencyGuard(
            idempotency_key,
            request,
            principal["user_id"],
            body_hash=upload_fingerprint,
        )
        cached = await guard.check()
        if cached:
            cached_file_id = cached.get("file_id")
            if cached_file_id:

                def _verify_cached_file() -> bool:
                    with db.get_connection() as conn:
                        cached_file = db.get_meeting_file(conn, cached_file_id)
                        return bool(
                            cached_file and cached_file.get("user_id") == principal["user_id"]
                        )

                if await asyncio.to_thread(_verify_cached_file):
                    completed = True
                    return UploadResponse(**cached)
                await guard.invalidate()
            else:
                completed = True
                return UploadResponse(**cached)

        # Resolve an existing target before checking per-meeting hash uniqueness.
        # A new meeting is created in the same SQL transaction as its first file
        # record below, so a process crash cannot leave a meeting-only commit.
        if meeting_id:
            target_meeting_id: int | None = meeting_id
            audit_action = "add_file"
        else:
            target_meeting_id = None
            audit_action = "create"

        # Reuse existing same-hash file in the same meeting
        def _check_meeting_hash():
            with db.get_connection() as conn:
                return (
                    db.get_meeting_file_by_hash(conn, content_hash, target_meeting_id)
                    if target_meeting_id is not None
                    else None
                )

        existing = await asyncio.to_thread(_check_meeting_hash)
        if existing:
            assert target_meeting_id is not None
            existing_msg = (
                "File already uploaded and still processing"
                if existing.get("status") == "processing"
                else "File already uploaded"
            )
            response = UploadResponse(
                meeting_id=target_meeting_id,
                file_id=existing["id"],
                message=existing_msg,
                is_existing=True,
            )
            await guard.save(response.model_dump(mode="json"))
            completed = True
            return response

        storage_id = uuid.uuid4().hex
        save_name = f"{storage_id}_{safe_name}"
        final_save_path = settings.UPLOAD_DIR / save_name
        save_path = final_save_path
        tmp_path = settings.UPLOAD_DIR / f".tmp-{storage_id}-{safe_name}"
        if not final_save_path.resolve().is_relative_to(settings.UPLOAD_DIR.resolve()):
            raise HTTPException(400, "Invalid file path")
        if not tmp_path.resolve().is_relative_to(settings.UPLOAD_DIR.resolve()):
            raise HTTPException(400, "Invalid file path")

        try:
            # Reserve the record first to guard concurrent same-hash uploads
            def _reserve_file_record() -> tuple[int, int | None, dict | None, bool]:
                with get_write_connection() as conn:
                    resolved_meeting_id = target_meeting_id
                    created = False
                    if resolved_meeting_id is None:
                        resolved_meeting_id = db.create_meeting(
                            conn,
                            title=title,
                            description=description,
                            user_id=principal["user_id"],
                        )
                        created = True
                    new_id = db.create_meeting_file_if_absent(
                        conn,
                        meeting_id=resolved_meeting_id,
                        file_type=file_type.value,
                        file_name=safe_name,
                        file_path=str(final_save_path),
                        content_hash=content_hash,
                        user_id=principal["user_id"],
                    )
                    if new_id is not None:
                        conn.execute(
                            "UPDATE meeting_files SET business_domain=?,"
                            "material_role=COALESCE(?,material_role) WHERE id=?",
                            (business_domain, material_role, new_id),
                        )
                        return resolved_meeting_id, new_id, None, created
                    return (
                        resolved_meeting_id,
                        None,
                        db.get_meeting_file_by_hash(conn, content_hash, resolved_meeting_id),
                        created,
                    )

            target_meeting_id, file_record_id, existing, created = await asyncio.to_thread(
                _reserve_file_record
            )
            if created:
                created_meeting_id = target_meeting_id
            if file_record_id is None and existing:
                response = UploadResponse(
                    meeting_id=target_meeting_id,
                    file_id=existing["id"],
                    message="File already uploaded and still processing",
                    is_existing=True,
                )
                await guard.save(response.model_dump(mode="json"))
                completed = True
                return response

            if file_record_id is None:
                raise RuntimeError("Failed to reserve meeting file record")

            # Atomically rename pre-written upload tmp to final path
            def _finalize_file() -> None:
                os.replace(incoming_path, final_save_path)
                os.chmod(final_save_path, 0o600)

            await asyncio.to_thread(_finalize_file)
        except HTTPException:
            _upload_status = "error"
            raise
        except Exception:
            _upload_status = "error"
            # Clean up orphaned DB/file records on write or DB failure
            if file_record_id is not None:
                with contextlib.suppress(Exception):

                    def _cleanup_record() -> None:
                        with get_write_connection() as conn:
                            db.delete_meeting_file(conn, file_record_id)

                    await asyncio.to_thread(_cleanup_record)
            incoming_path.unlink(missing_ok=True)
            tmp_path.unlink(missing_ok=True)
            final_save_path.unlink(missing_ok=True)
            raise
        safe_raw = raw_filename.replace(chr(10), chr(92) + "n").replace(chr(13), chr(92) + "r")[
            :256
        ]
        audit_log(
            audit_action,
            "meeting",
            target_meeting_id,
            detail=(f"file={safe_name} raw_name={safe_raw} type={file_type.value}"),
        )

        response = UploadResponse(
            meeting_id=target_meeting_id,
            file_id=file_record_id,
            message="File uploaded successfully, processing in background",
            is_existing=False,
        )
        await guard.save(response.model_dump(mode="json"))
        # Schedule only after all fallible response persistence has completed.
        from ....services.processor import schedule_meeting_file_processing

        await schedule_meeting_file_processing(file_record_id)
        completed = True
        return response
    except HTTPException:
        _upload_status = "error"
        raise
    except Exception:
        _upload_status = "error"
        raise
    finally:
        # The pre-write temporary file is owned by this request until the
        # atomic rename succeeds.  All early returns and failures converge
        # here, preventing .upload-* accumulation in long-running processes.
        if tmp_upload_path is not None:
            with contextlib.suppress(OSError):
                tmp_upload_path.unlink(missing_ok=True)

        if not completed and (file_record_id is not None or created_meeting_id is not None):

            def _rollback_incomplete_upload() -> None:
                with get_write_connection() as conn:
                    if created_meeting_id is not None:
                        db.delete_meeting(
                            conn,
                            created_meeting_id,
                            user_id=principal["user_id"],
                        )
                    elif file_record_id is not None:
                        db.delete_meeting_file(
                            conn,
                            file_record_id,
                            user_id=principal["user_id"],
                        )

            with contextlib.suppress(Exception):
                await asyncio.to_thread(_rollback_incomplete_upload)
            if save_path is not None:
                with contextlib.suppress(OSError):
                    save_path.unlink(missing_ok=True)

        with contextlib.suppress(Exception):
            MEETING_UPLOAD_TOTAL.labels(status=_upload_status).inc()

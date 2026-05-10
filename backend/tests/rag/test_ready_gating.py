"""Tests for ready-gating: post-ready summary scheduling and meeting finalization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.anyio
async def test_schedule_skips_when_summary_already_ready():
    """Idempotent: no-op if summary_status is already 'ready'."""
    file_row = {
        "id": 1,
        "meeting_id": 10,
        "file_type": "pdf",
        "file_name": "doc.pdf",
        "transcript": "text",
        "summary_status": "ready",
        "segments_json": None,
    }
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch("src.core.database.get_meeting_file", return_value=file_row),
        patch("src.core.database.update_file_summary_status") as mock_update,
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import schedule_post_ready_summary

        await schedule_post_ready_summary(1, 10)

    mock_update.assert_not_called()


@pytest.mark.anyio
async def test_schedule_marks_ready_when_no_transcript():
    """Files without transcript get summary_status='ready' immediately."""
    file_row = {
        "id": 2,
        "meeting_id": 10,
        "file_type": "image",
        "file_name": "img.png",
        "transcript": None,
        "summary_status": "pending",
        "segments_json": None,
    }
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch("src.core.database.get_meeting_file", return_value=file_row),
        patch("src.core.database.update_file_summary_status") as mock_update,
        patch(
            "src.services.processor._pipeline_common._maybe_finalize_meeting_summary",
            new_callable=AsyncMock,
        ),
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import schedule_post_ready_summary

        await schedule_post_ready_summary(2, 10)

    mock_update.assert_any_call(2, "ready")


@pytest.mark.anyio
async def test_schedule_generates_and_persists_summary():
    """Happy path: generate summary, persist, set status to ready."""
    file_row = {
        "id": 3,
        "meeting_id": 10,
        "file_type": "pdf",
        "file_name": "doc.pdf",
        "transcript": "The meeting discussed project deadlines.",
        "summary_status": "pending",
        "segments_json": None,
    }
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None
    mock_generate = AsyncMock(return_value=("Summary text", ["point 1"]))

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch("src.core.database.get_meeting_file", return_value=file_row),
        patch("src.core.database.update_file_summary_status") as mock_update,
        patch("src.services.processor._pipeline_common._persist_file_summary") as mock_persist,
        patch(
            "src.services.processor._pipeline_common._ws_notify_summary_status",
            new_callable=AsyncMock,
        ),
        patch(
            "src.services.chain._per_file_summary.generate_per_file_summary",
            mock_generate,
        ),
        patch(
            "src.services.processor._pipeline_common._maybe_finalize_meeting_summary",
            new_callable=AsyncMock,
        ),
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import schedule_post_ready_summary

        await schedule_post_ready_summary(3, 10)

    calls = mock_update.call_args_list
    assert any(c[0] == (3, "generating") for c in calls)
    assert any(c[0] == (3, "ready") for c in calls)
    mock_persist.assert_called_once_with(3, "Summary text", ["point 1"], meeting_id=10)


@pytest.mark.anyio
async def test_schedule_marks_failed_on_exception():
    """On generation failure, summary_status should be 'failed'."""
    file_row = {
        "id": 4,
        "meeting_id": 10,
        "file_type": "pdf",
        "file_name": "doc.pdf",
        "transcript": "text",
        "summary_status": "pending",
        "segments_json": None,
    }
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch("src.core.database.get_meeting_file", return_value=file_row),
        patch("src.core.database.update_file_summary_status") as mock_update,
        patch(
            "src.services.processor._pipeline_common._ws_notify_summary_status",
            new_callable=AsyncMock,
        ),
        patch(
            "src.services.chain._per_file_summary.generate_per_file_summary",
            side_effect=RuntimeError("LLM down"),
        ),
        patch(
            "src.services.processor._pipeline_common._maybe_finalize_meeting_summary",
            new_callable=AsyncMock,
        ),
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import schedule_post_ready_summary

        await schedule_post_ready_summary(4, 10)

    calls = mock_update.call_args_list
    assert any(c[0] == (4, "failed") for c in calls)


def _make_finalize_conn(summarizing_count: int, summary_rows: list) -> MagicMock:
    """Build a mock connection for _maybe_finalize_meeting_summary.

    The function issues two sequential execute() calls:
      1. COUNT(*) for files in 'summarizing' status → fetchone()["cnt"]
      2. summary_status for files in 'ready' status → fetchall()
    """
    cnt_mock = MagicMock()
    cnt_mock.fetchone.return_value = {"cnt": summarizing_count}

    rows_mock = MagicMock()
    rows_mock.fetchall.return_value = summary_rows

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [cnt_mock, rows_mock]
    return mock_conn


@pytest.mark.anyio
async def test_maybe_finalize_skips_when_pending_files():
    """Don't trigger meeting summary if some files still have pending summary_status."""
    mock_conn = _make_finalize_conn(
        summarizing_count=0,
        summary_rows=[{"summary_status": "ready"}, {"summary_status": "pending"}],
    )

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch(
            "src.services.processor._pipeline_common._maybe_trigger_meeting_summary"
        ) as mock_trigger,
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import _maybe_finalize_meeting_summary

        await _maybe_finalize_meeting_summary(10)

    mock_trigger.assert_not_called()


@pytest.mark.anyio
async def test_maybe_finalize_skips_when_still_summarizing():
    """Don't trigger meeting summary if any file is still in 'summarizing' status."""
    # When summarizing_count > 0, the function returns before the second query.
    cnt_mock = MagicMock()
    cnt_mock.fetchone.return_value = {"cnt": 1}
    mock_conn = MagicMock()
    mock_conn.execute.return_value = cnt_mock

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch(
            "src.services.processor._pipeline_common._maybe_trigger_meeting_summary"
        ) as mock_trigger,
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import _maybe_finalize_meeting_summary

        await _maybe_finalize_meeting_summary(10)

    mock_trigger.assert_not_called()


@pytest.mark.anyio
async def test_maybe_finalize_triggers_when_all_terminal():
    """Trigger meeting summary when all files have ready/failed summary_status."""
    mock_conn = _make_finalize_conn(
        summarizing_count=0,
        summary_rows=[{"summary_status": "ready"}, {"summary_status": "failed"}],
    )

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch(
            "src.services.processor._pipeline_common._maybe_trigger_meeting_summary"
        ) as mock_trigger,
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import _maybe_finalize_meeting_summary

        await _maybe_finalize_meeting_summary(10)

    mock_trigger.assert_called_once_with(10)


@pytest.mark.anyio
async def test_maybe_finalize_noop_when_no_files():
    """No ready files means nothing to finalize."""
    mock_conn = _make_finalize_conn(summarizing_count=0, summary_rows=[])

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch(
            "src.services.processor._pipeline_common._maybe_trigger_meeting_summary"
        ) as mock_trigger,
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import _maybe_finalize_meeting_summary

        await _maybe_finalize_meeting_summary(10)

    mock_trigger.assert_not_called()


@pytest.mark.anyio
async def test_schedule_passes_segments_for_video():
    """Video files should have segments_json passed to generate_per_file_summary."""
    segments = [{"start": 0, "end": 5, "speaker": "A", "text": "hi"}]
    file_row = {
        "id": 5,
        "meeting_id": 10,
        "file_type": "video",
        "file_name": "vid.mp4",
        "transcript": "some transcript",
        "summary_status": "pending",
        "segments_json": '[{"start":0,"end":5,"speaker":"A","text":"hi"}]',
    }
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None
    mock_generate = AsyncMock(return_value=("Summary", []))

    with (
        patch("src.core.database.get_connection") as mock_get_conn,
        patch("src.core.database.get_meeting_file", return_value=file_row),
        patch("src.core.database.update_file_summary_status"),
        patch("src.services.processor._pipeline_common._persist_file_summary"),
        patch(
            "src.services.processor._pipeline_common._ws_notify_summary_status",
            new_callable=AsyncMock,
        ),
        patch(
            "src.services.chain._per_file_summary.generate_per_file_summary",
            mock_generate,
        ),
        patch(
            "src.services.processor._pipeline_common._maybe_finalize_meeting_summary",
            new_callable=AsyncMock,
        ),
    ):
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline_common import schedule_post_ready_summary

        await schedule_post_ready_summary(5, 10)

    mock_generate.assert_called_once()
    assert mock_generate.call_args.kwargs["segments"] == segments


# ---------------------------------------------------------------------------
# _update_meeting_status_from_files
# ---------------------------------------------------------------------------


def test_update_meeting_status_to_summarizing_when_summary_not_ready():
    """Meeting → 'summarizing' when all files ready, no errors, summary_status != 'ready'."""
    mock_conn = MagicMock()
    # get_meeting_file_status_counts returns only ready files
    file_counts = {"ready": 2}

    # Second execute: SELECT summary_status → not ready
    summary_row_mock = MagicMock()
    summary_row_mock.fetchone.return_value = {"summary_status": "pending"}

    mock_conn.execute.side_effect = [summary_row_mock]

    with (
        patch(
            "src.services.processor._pipeline_common.get_meeting_file_status_counts",
            return_value=file_counts,
        ),
        patch(
            "src.services.processor._pipeline_common.update_meeting_status",
        ) as mock_update_status,
    ):
        from src.services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        _update_meeting_status_from_files(mock_conn, 10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "summarizing")


def test_update_meeting_status_to_ready_when_summary_already_ready():
    """Meeting → 'ready' when all files ready, no errors, summary_status == 'ready'."""
    mock_conn = MagicMock()
    file_counts = {"ready": 3}

    # Second execute: SELECT summary_status → 'ready'
    summary_row_mock = MagicMock()
    summary_row_mock.fetchone.return_value = {"summary_status": "ready"}

    mock_conn.execute.side_effect = [summary_row_mock]

    with (
        patch(
            "src.services.processor._pipeline_common.get_meeting_file_status_counts",
            return_value=file_counts,
        ),
        patch(
            "src.services.processor._pipeline_common.update_meeting_status",
        ) as mock_update_status,
    ):
        from src.services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        _update_meeting_status_from_files(mock_conn, 10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "ready")


def test_update_meeting_status_summarizing_when_files_still_summarizing():
    """Meeting → 'summarizing' when no file is parsing but at least one
    is generating its per-file summary."""
    mock_conn = MagicMock()
    file_counts = {"ready": 1, "summarizing": 1}

    with (
        patch(
            "src.services.processor._pipeline_common.get_meeting_file_status_counts",
            return_value=file_counts,
        ),
        patch(
            "src.services.processor._pipeline_common.update_meeting_status",
        ) as mock_update_status,
    ):
        from src.services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        _update_meeting_status_from_files(mock_conn, 10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "summarizing")


def test_update_meeting_status_processing_when_files_still_processing():
    """Meeting → 'processing' as long as any file is still extracting content."""
    mock_conn = MagicMock()
    file_counts = {"ready": 1, "processing": 1, "summarizing": 1}

    with (
        patch(
            "src.services.processor._pipeline_common.get_meeting_file_status_counts",
            return_value=file_counts,
        ),
        patch(
            "src.services.processor._pipeline_common.update_meeting_status",
        ) as mock_update_status,
    ):
        from src.services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        _update_meeting_status_from_files(mock_conn, 10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "processing")


def test_update_meeting_status_failed_when_errors_present():
    """Meeting → 'failed' when at least one file is in error (even with ready files)."""
    mock_conn = MagicMock()
    file_counts = {"ready": 2, "error": 1}

    with (
        patch(
            "src.services.processor._pipeline_common.get_meeting_file_status_counts",
            return_value=file_counts,
        ),
        patch(
            "src.services.processor._pipeline_common.update_meeting_status",
        ) as mock_update_status,
    ):
        from src.services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        _update_meeting_status_from_files(mock_conn, 10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "failed")


def test_update_meeting_status_summarizing_without_auto_summarize():
    """Meeting → 'ready' (not summarizing) when MEETING_AUTO_SUMMARIZE_FILES is off."""
    mock_conn = MagicMock()
    file_counts = {"ready": 2}

    with (
        patch(
            "src.services.processor._pipeline_common.get_meeting_file_status_counts",
            return_value=file_counts,
        ),
        patch(
            "src.services.processor._pipeline_common.settings",
        ) as mock_settings,
        patch(
            "src.services.processor._pipeline_common.update_meeting_status",
        ) as mock_update_status,
    ):
        mock_settings.MEETING_AUTO_SUMMARIZE_FILES = False

        from src.services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        _update_meeting_status_from_files(mock_conn, 10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "ready")


# ---------------------------------------------------------------------------
# _mark_auto_failed
# ---------------------------------------------------------------------------


def test_mark_auto_failed_from_summarizing():
    """_mark_auto_failed flips meeting from 'summarizing' → 'failed'."""
    fetch_mock = MagicMock()
    fetch_mock.fetchone.return_value = {"status": "summarizing"}

    mock_conn = MagicMock()
    mock_conn.execute.return_value = fetch_mock

    with (
        patch(
            "src.services.processor._pipeline.get_write_connection",
        ) as mock_get_write_conn,
        patch(
            "src.services.processor._pipeline.update_meeting_status",
        ) as mock_update_status,
    ):
        mock_get_write_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_write_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline import _mark_auto_failed

        _mark_auto_failed(10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "failed")
    # Verify summary_status is also set to 'failed'
    update_calls = [
        c for c in mock_conn.execute.call_args_list if "summary_status='failed'" in str(c)
    ]
    assert len(update_calls) == 1


def test_mark_auto_failed_from_ready():
    """_mark_auto_failed flips meeting from 'ready' → 'failed'."""
    fetch_mock = MagicMock()
    fetch_mock.fetchone.return_value = {"status": "ready"}

    mock_conn = MagicMock()
    mock_conn.execute.return_value = fetch_mock

    with (
        patch(
            "src.services.processor._pipeline.get_write_connection",
        ) as mock_get_write_conn,
        patch(
            "src.services.processor._pipeline.update_meeting_status",
        ) as mock_update_status,
    ):
        mock_get_write_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_write_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline import _mark_auto_failed

        _mark_auto_failed(10)

    mock_update_status.assert_called_once_with(mock_conn, 10, "failed")


def test_mark_auto_failed_noop_when_not_summarizing_or_ready():
    """_mark_auto_failed does not flip status when meeting is in a non-target state."""
    fetch_mock = MagicMock()
    fetch_mock.fetchone.return_value = {"status": "processing"}

    mock_conn = MagicMock()
    mock_conn.execute.return_value = fetch_mock

    with (
        patch(
            "src.services.processor._pipeline.get_write_connection",
        ) as mock_get_write_conn,
        patch(
            "src.services.processor._pipeline.update_meeting_status",
        ) as mock_update_status,
    ):
        mock_get_write_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_write_conn.return_value.__exit__ = MagicMock(return_value=False)

        from src.services.processor._pipeline import _mark_auto_failed

        _mark_auto_failed(10)

    mock_update_status.assert_not_called()

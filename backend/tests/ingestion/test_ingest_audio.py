"""Integration tests for audio upload and background processing pipeline."""

import io
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import get_connection
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestAudioUploadAndProcess:
    """Verify audio files upload successfully and are routed to ASR pipeline."""

    @pytest.mark.asyncio
    async def test_upload_mp3_routed_to_transcription(self, client, auth_headers):
        """Upload an MP3 file; background task should call transcribe_with_timestamps."""
        fake_segments = [
            {
                "start": 0.0,
                "end": 5.0,
                "text": "Hello from audio test. This is a long enough segment to pass the minimum length check.",
            },
        ]

        with (
            patch(
                "src.services.processor._pipeline.transcribe_with_timestamps",
                new_callable=AsyncMock,
                return_value=fake_segments,
            ) as mock_transcribe,
            patch("src.services.processor._pipeline.index_meeting_segments") as mock_index,
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/meetings/upload",
                    headers=auth_headers,
                    data={"title": "Audio Upload Test"},
                    files={
                        "file": (
                            "test.mp3",
                            io.BytesIO(b"\xff\xfb" + b"fake_mp3_data" * 100),
                            "audio/mpeg",
                        )
                    },
                )
            assert resp.status_code == 200
            data = resp.json()
            file_id = data["file_id"]

            # ASGITransport runs BackgroundTasks automatically after response
            mock_transcribe.assert_awaited_once()
            call_args = mock_transcribe.call_args
            assert call_args[1]["provider"] is not None

            # Indexer should have been called with segments
            mock_index.assert_called_once()
            index_args = mock_index.call_args[1]
            assert index_args["segments"] == fake_segments

            # File status should be ready
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT status, transcript FROM meeting_files WHERE id = ?",
                    (file_id,),
                ).fetchone()
            assert row["status"] == "ready"
            assert "Hello from audio test." in row["transcript"]

    @pytest.mark.asyncio
    async def test_upload_wav_routed_to_transcription(self, client, auth_headers):
        """Upload a WAV file; background task should call transcribe_with_timestamps."""
        fake_segments = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "WAV audio content here. This segment is definitely longer than fifty characters.",
            },
        ]

        with (
            patch(
                "src.services.processor._pipeline.transcribe_with_timestamps",
                new_callable=AsyncMock,
                return_value=fake_segments,
            ) as mock_transcribe,
            patch("src.services.processor._pipeline.index_meeting_segments") as mock_index,
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/meetings/upload",
                    headers=auth_headers,
                    data={"title": "WAV Upload Test"},
                    files={
                        "file": (
                            "test.wav",
                            io.BytesIO(b"RIFF" + b"fake_wav_data" * 100),
                            "audio/wav",
                        )
                    },
                )
            assert resp.status_code == 200
            data = resp.json()
            file_id = data["file_id"]

            mock_transcribe.assert_awaited_once()
            mock_index.assert_called_once()

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT status FROM meeting_files WHERE id = ?", (file_id,)
                ).fetchone()
            assert row["status"] == "ready"

    @pytest.mark.asyncio
    async def test_upload_m4a_magic_bytes_pass(self, auth_headers):
        """Upload an M4A file with varying ftyp box sizes and brands — should pass validation.

        Real-world M4A files use many brand strings (M4A, isom, mp42, M4B, mp41, ...)
        and varying ftyp box sizes. The validator must accept any ftyp box.
        """
        for header in [
            # Historically-covered M4A brand variants
            b"\x00\x00\x00\x18ftypM4A ",
            b"\x00\x00\x00\x1cftypM4A ",
            b"\x00\x00\x00 ftypM4A ",
            # Real-world variants that used to incorrectly fail validation
            b"\x00\x00\x00\x20ftypisom",  # iTunes/ffmpeg default brand
            b"\x00\x00\x00\x1cftypmp42",  # common AAC-in-MP4 brand
            b"\x00\x00\x00\x18ftypM4B ",  # audiobook brand
            b"\x00\x00\x00$ftypmp42",  # larger ftyp box size
        ]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                with (
                    patch(
                        "src.services.processor._pipeline.transcribe_with_timestamps",
                        new_callable=AsyncMock,
                        return_value=[
                            {
                                "start": 0.0,
                                "end": 1.0,
                                "text": "This is a long enough segment to pass the minimum length check.",
                            }
                        ],
                    ),
                    patch("src.services.processor._pipeline.index_meeting_segments"),
                ):
                    resp = await c.post(
                        "/api/v1/meetings/upload",
                        headers=auth_headers,
                        data={"title": "M4A Test"},
                        files={
                            "file": (
                                "test.m4a",
                                io.BytesIO(header + b"padding" * 50),
                                "audio/mp4",
                            )
                        },
                    )
                assert resp.status_code == 200, f"M4A header {header!r} should pass"

    @pytest.mark.asyncio
    async def test_upload_mp3_sync_word_variants_pass(self, auth_headers):
        """Upload MP3 files with every common sync-word variant — all should pass.

        The MPEG audio sync word is 11 bits all 1: byte0 == 0xff and
        (byte1 & 0xe0) == 0xe0. The validator must accept all variants, not just
        a hardcoded handful.
        """
        # MPEG-1/2/2.5 Layer I/II/III — all start with 0xff followed by a byte
        # whose top 3 bits are set. \xff\xfa is MPEG-1 Layer III with CRC, which
        # is extremely common but used to be rejected.
        for second_byte in (b"\xfa", b"\xfb", b"\xf2", b"\xf3", b"\xe2", b"\xe3"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                with (
                    patch(
                        "src.services.processor._pipeline.transcribe_with_timestamps",
                        new_callable=AsyncMock,
                        return_value=[
                            {
                                "start": 0.0,
                                "end": 1.0,
                                "text": (
                                    "This is a long enough segment to pass "
                                    "the minimum length check."
                                ),
                            }
                        ],
                    ),
                    patch("src.services.processor._pipeline.index_meeting_segments"),
                ):
                    resp = await c.post(
                        "/api/v1/meetings/upload",
                        headers=auth_headers,
                        data={"title": "MP3 Sync Test"},
                        files={
                            "file": (
                                "test.mp3",
                                io.BytesIO(b"\xff" + second_byte + b"x" * 200),
                                "audio/mpeg",
                            )
                        },
                    )
                assert resp.status_code == 200, (
                    f"MP3 sync word \\xff{second_byte.hex()} should pass"
                )

    @pytest.mark.asyncio
    async def test_audio_transcription_failure_marked_error(self, client, auth_headers):
        """If transcription raises an exception, file status should become error."""
        with patch(
            "src.services.processor._pipeline.transcribe_with_timestamps",
            new_callable=AsyncMock,
            side_effect=RuntimeError("assemblyai request failed"),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/meetings/upload",
                    headers=auth_headers,
                    data={"title": "Failing Audio"},
                    files={
                        "file": (
                            "test.mp3",
                            io.BytesIO(b"\xff\xfb" + b"x" * 100),
                            "audio/mpeg",
                        )
                    },
                )
            assert resp.status_code == 200
            file_id = resp.json()["file_id"]

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT status, error_message FROM meeting_files WHERE id = ?",
                    (file_id,),
                ).fetchone()
            assert row["status"] == "error"
            assert "assemblyai request failed" in row["error_message"]

"""Audio/video transcription service — AssemblyAI only.

Local ASR providers (Whisper, VibeVoice) were removed to keep the Docker
image lean and avoid pulling PyTorch + CUDA. Transcription is delegated to
AssemblyAI via an async httpx client in ``.asr._assemblyai``.
"""

import asyncio
import contextlib
import logging
import tempfile
from pathlib import Path

from ..core.trace import TraceContext
from .asr._assemblyai import transcribe_assemblyai, transcribe_assemblyai_with_segments
from .files._kinds import video_extensions

logger = logging.getLogger(__name__)

# ffmpeg timeout in seconds
_FFMPEG_TIMEOUT = 300

# Video file extensions (need audio extraction)
_VIDEO_EXTS = set(video_extensions())

# The single supported ASR provider. Kept as a constant so callers can still
# pass ``provider=`` explicitly without special-casing the name elsewhere.
_SUPPORTED_PROVIDER = "assemblyai"


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------


def extract_audio(video_path: Path) -> Path:
    """Extract / convert audio to 16 kHz mono 16-bit PCM WAV via ffmpeg.

    Writes to a temporary file that the caller is responsible for deleting.
    AssemblyAI accepts video, but a 16 kHz mono WAV is ~10x smaller on the
    wire and uploads faster.
    """
    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        output_path = Path(tmp.name)

    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vn",  # no video
        "-acodec",
        "pcm_s16le",  # 16-bit PCM
        "-ar",
        "16000",  # 16 kHz (standard ASR sample rate)
        "-ac",
        "1",  # mono
        "-y",  # overwrite existing
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    except Exception:
        # Clean up on failure
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def _check_provider(provider: str) -> None:
    if provider != _SUPPORTED_PROVIDER:
        raise ValueError(
            f"Unsupported ASR provider: {provider!r}. Only {_SUPPORTED_PROVIDER!r} is supported."
        )


# ---------------------------------------------------------------------------
# Unified entry points
# ---------------------------------------------------------------------------


async def transcribe(
    file_path: Path,
    provider: str = _SUPPORTED_PROVIDER,
    trace: TraceContext | None = None,
    **_: object,
) -> str:
    """Transcribe an audio/video file to plain text via AssemblyAI.

    Args:
        file_path: audio/video file path.
        provider: must be ``"assemblyai"`` (kept for API compatibility).
        trace: optional trace context for benchmarking.
    """
    _check_provider(provider)

    is_video = file_path.suffix.lower() in _VIDEO_EXTS
    file_size = file_path.stat().st_size if file_path.exists() else 0

    if trace:
        trace.start_span(
            "transcribe",
            "extract",
            provider=provider,
            file_size_bytes=file_size,
            is_video=is_video,
        )

    audio_path: Path | None = None
    temp_audio_path: Path | None = None
    try:
        if is_video:
            temp_audio_path = await asyncio.to_thread(extract_audio, file_path)
            audio_path = temp_audio_path
        else:
            audio_path = file_path
        assert audio_path is not None
        result = await transcribe_assemblyai(audio_path)
        if trace:
            trace.finish_span("transcribe")
        return result
    except Exception:
        if trace:
            trace.finish_span("transcribe", "error")
        raise
    finally:
        # Clean up temporary WAV extracted from video.
        # Use temp_audio_path (assigned before audio_path) so that cleanup
        # still works if the assignment to audio_path was interrupted.
        if temp_audio_path is not None:
            with contextlib.suppress(OSError):
                temp_audio_path.unlink(missing_ok=True)


async def transcribe_with_timestamps(
    file_path: Path,
    provider: str = _SUPPORTED_PROVIDER,
    trace: TraceContext | None = None,
    **_: object,
) -> list[dict]:
    """Transcribe with timestamps for each segment via AssemblyAI.

    Returns list of segments: ``[{"start": 0.0, "end": 5.2, "text": "..."}, ...]``.

    Args:
        file_path: audio/video file path.
        provider: must be ``"assemblyai"`` (kept for API compatibility).
        trace: optional trace context for benchmarking.
    """
    _check_provider(provider)

    is_video = file_path.suffix.lower() in _VIDEO_EXTS
    file_size = file_path.stat().st_size if file_path.exists() else 0

    if trace:
        trace.start_span(
            "transcribe",
            "extract",
            provider=provider,
            file_size_bytes=file_size,
            is_video=is_video,
        )

    audio_path: Path | None = None
    temp_audio_path: Path | None = None
    try:
        if is_video:
            temp_audio_path = await asyncio.to_thread(extract_audio, file_path)
            audio_path = temp_audio_path
        else:
            audio_path = file_path
        assert audio_path is not None
        segments = await transcribe_assemblyai_with_segments(audio_path)
        if trace:
            trace.finish_span("transcribe")
        return segments
    except Exception:
        if trace:
            trace.finish_span("transcribe", "error")
        raise
    finally:
        if temp_audio_path is not None:
            with contextlib.suppress(OSError):
                temp_audio_path.unlink(missing_ok=True)


__all__ = [
    "extract_audio",
    "transcribe",
    "transcribe_with_timestamps",
]

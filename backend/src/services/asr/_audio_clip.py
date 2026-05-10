"""Extract short audio clips from source files for speaker sample playback."""

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum clip length in seconds to prevent accidental large extractions
_MAX_CLIP_SECONDS = 30.0
_FFMPEG_TIMEOUT = 30


def extract_audio_clip(
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
    padding_seconds: float = 0.5,
) -> Path:
    """Extract a short audio clip from a source file as WAV.

    Pads the start/end by ``padding_seconds`` (clamped to 0) and caps
    clip length at 30 seconds.  Returns a temp file path — the caller
    is responsible for deleting it after use.

    Args:
        source_path: Path to the source audio/video file.
        start_seconds: Start time in seconds.
        end_seconds: End time in seconds.
        padding_seconds: Padding before/after the segment.

    Returns:
        Path to a temporary WAV file.
    """
    # Apply padding (clamped to 0)
    padded_start = max(0.0, start_seconds - padding_seconds)
    padded_end = end_seconds + padding_seconds

    # Cap clip length
    clip_duration = padded_end - padded_start
    if clip_duration > _MAX_CLIP_SECONDS:
        padded_end = padded_start + _MAX_CLIP_SECONDS

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        output_path = Path(tmp.name)

    cmd = [
        "ffmpeg",
        "-ss",
        f"{padded_start:.3f}",
        "-to",
        f"{padded_end:.3f}",
        "-i",
        str(source_path),
        "-vn",  # no video
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",  # mono
        "-y",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    except Exception:
        output_path.unlink(missing_ok=True)
        logger.warning(
            "Failed to extract audio clip from %s (%.1f-%.1fs)",
            source_path.name,
            padded_start,
            padded_end,
            exc_info=True,
        )
        raise
    return output_path

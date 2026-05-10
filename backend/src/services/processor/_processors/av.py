"""Audio/video processor."""

import asyncio
import contextlib
import json
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from ....core.config import settings
from ....core.trace import TraceContext
from ...transcriber import transcribe_with_timestamps
from ...vision import caption_image
from ._types import FileArtefact, ProcessorContext


class AVFileProcessor:
    kind = "av"

    def __init__(
        self,
        transcriber: Callable[[Path, str, object | None], Awaitable[list[dict]]] | None = None,
    ) -> None:
        self._transcriber = transcriber or _default_transcriber

    async def process(self, ctx: ProcessorContext) -> FileArtefact:
        segments = await self._transcriber(ctx.file_path, settings.ASR_PROVIDER, ctx.trace)
        lines: list[str] = []
        for seg in segments:
            speaker = seg.get("speaker")
            text_part = seg.get("text", "").strip()
            if speaker:
                lines.append(f"{speaker}: {text_part}")
            elif text_part:
                lines.append(text_part)
        text = "\n".join(lines)
        duration = float(segments[-1].get("end", 0.0)) if segments else 0.0
        speakers = {s.get("speaker") for s in segments if s.get("speaker")}
        metrics: dict[str, int | float | str] = {
            "duration_seconds": duration,
            "word_count": len(text.split()),
            "speaker_count": len(speakers),
        }
        aux_segments = await _build_keyframe_segments(ctx, duration)
        return FileArtefact(
            text=text,
            structured_json=json.dumps(segments),
            structured_kind="segments",
            metrics=metrics,
            segments=segments,
            aux_segments=aux_segments,
            parsed_doc=None,
        )


async def _default_transcriber(file_path: Path, provider: str, trace: object | None) -> list[dict]:
    typed_trace = trace if isinstance(trace, TraceContext) else None
    return await transcribe_with_timestamps(file_path, provider=provider, trace=typed_trace)


def _ts_label(seconds: float) -> str:
    total = int(max(seconds, 0.0))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


async def _extract_frame(video_path: Path, second: float) -> Path | None:
    def _run() -> Path | None:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = Path(tmp.name)
        cmd = [
            "ffmpeg",
            "-ss",
            str(second),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(frame_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            return frame_path
        except Exception:
            frame_path.unlink(missing_ok=True)
            return None

    return await asyncio.to_thread(_run)


async def _build_keyframe_segments(
    ctx: ProcessorContext, duration_seconds: float
) -> list[dict] | None:
    if ctx.file_type != "video" or not settings.VIDEO_KEYFRAMES_ENABLED:
        return None
    interval_seconds = 30
    if duration_seconds <= interval_seconds:
        return None

    keyframes: list[dict] = []
    t = interval_seconds
    while t < duration_seconds:
        caption = None
        frame_path: Path | None = None
        if settings.MULTIMODAL_CAPTIONING_ENABLED:
            frame_path = await _extract_frame(ctx.file_path, float(t))
            if frame_path:
                caption = await caption_image(str(frame_path))
        text = caption or f"Keyframe at {_ts_label(float(t))}"
        keyframes.append(
            {
                "start": float(t),
                "end": float(t) + 0.1,
                "text": text,
                "speaker": None,
            }
        )
        if frame_path:
            with contextlib.suppress(OSError):
                frame_path.unlink(missing_ok=True)
        t += interval_seconds
    return keyframes or None

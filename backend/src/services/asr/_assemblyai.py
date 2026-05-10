"""Async AssemblyAI client for speech-to-text with speaker diarization.

Provides:
- transcribe_assemblyai: plain text transcription
- transcribe_assemblyai_with_segments: segments with timestamps and optional speaker labels

Uses a shared httpx.AsyncClient singleton (thread-safe) following the same pattern
as services/search.py.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from ...core.config import settings
from ...core.http_client import LoopBoundAsyncClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.assemblyai.com"

_http_client = LoopBoundAsyncClient(
    lambda: httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        limits=httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
        ),
    )
)

# httpx exceptions that indicate a transient network failure worth retrying.
# Excludes auth/4xx errors (handled separately via HTTPStatusError filter).
_TRANSIENT_HTTPX_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
)

_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 1.0


async def _retry_transient[T](
    label: str,
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = _RETRY_MAX_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY_S,
) -> T:
    """Retry *fn* on transient httpx network errors and 5xx / 429 responses.

    Permanent failures (auth, 4xx other than 429, malformed payload) propagate
    immediately so the caller can surface them without burning the budget.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except _TRANSIENT_HTTPX_EXCEPTIONS as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status >= 500 or status == 429:
                last_exc = exc
            else:
                raise
        if attempt < max_attempts - 1:
            delay = base_delay * (2**attempt)
            logger.warning(
                "AssemblyAI %s: %s — retry %d/%d in %.1fs",
                label,
                last_exc,
                attempt + 1,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _get_http_client() -> httpx.AsyncClient:
    """Get or create the shared loop-bound httpx AsyncClient."""
    return _http_client.get()


async def close_http_client() -> None:
    """Close the shared httpx client (call on shutdown)."""
    await _http_client.close()


def reset_http_client() -> None:
    """Reset client so next call creates a fresh one (for settings hot-reload)."""
    _http_client.reset()


def _api_key() -> str:
    """Return the AssemblyAI API key (empty string if not configured)."""
    return settings.ASSEMBLYAI_API_KEY.get_secret_value()


def _headers() -> dict[str, str]:
    return {"authorization": _api_key()}


# ---------------------------------------------------------------------------
# Internal pipeline steps
# ---------------------------------------------------------------------------


async def _upload(file_path: Path) -> str:
    """Stream a local file to AssemblyAI and return the upload_url.

    Retries transient network failures; the file iterator is recreated each
    attempt because async generators are single-use.
    """
    client = _get_http_client()

    async def _file_iter(p: Path, chunk_size: int = 5 * 1024 * 1024):
        with open(p, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    async def _attempt() -> str:
        response = await client.post(
            f"{BASE_URL}/v2/upload",
            headers=_headers(),
            content=_file_iter(file_path),
        )
        response.raise_for_status()
        return response.json()["upload_url"]

    return await _retry_transient("upload", _attempt)


async def _submit(audio_url: str) -> str:
    """Submit a transcription job and return the transcript_id."""
    client = _get_http_client()
    payload: dict = {
        "audio_url": audio_url,
        "language_detection": settings.ASSEMBLYAI_LANGUAGE_DETECTION,
        "speaker_labels": settings.ASSEMBLYAI_SPEAKER_LABELS,
    }
    speech_model = settings.ASSEMBLYAI_SPEECH_MODEL
    if speech_model:
        payload["speech_models"] = [speech_model]

    async def _attempt() -> str:
        response = await client.post(
            f"{BASE_URL}/v2/transcript",
            headers=_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if "id" not in data:
            raise RuntimeError("AssemblyAI submit returned unexpected response (no id)")
        return data["id"]

    return await _retry_transient("submit", _attempt)


async def _poll(transcript_id: str) -> dict:
    """Poll until transcription completes or times out.

    Each individual GET is retried on transient failures so a single network
    blip mid-poll does not abort the whole transcription.
    """
    client = _get_http_client()
    url = f"{BASE_URL}/v2/transcript/{transcript_id}"
    interval = settings.ASSEMBLYAI_POLL_INTERVAL_SECONDS
    max_wait = settings.ASSEMBLYAI_MAX_WAIT_SECONDS
    elapsed = 0.0

    async def _fetch_once() -> httpx.Response:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        return resp

    while True:
        response = await _retry_transient("poll", _fetch_once)
        result = response.json()
        status = result.get("status")

        if status == "completed":
            return result
        if status == "error":
            raise RuntimeError(
                f"AssemblyAI transcription failed: {result.get('error', 'Unknown error')}"
            )

        elapsed += interval
        if elapsed >= max_wait:
            raise TimeoutError(f"AssemblyAI polling timed out after {max_wait}s")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


# AssemblyAI occasionally emits a literal "[Speaker]" placeholder inside utterance
# text when its diarization model is uncertain about the speaker boundary. Strip
# these artifacts at the ASR boundary so they never reach chunks or the UI.
_SPEAKER_ARTIFACT_RE = re.compile(r"\s*\[Speaker\]\s*", re.IGNORECASE)


def _clean_utterance_text(text: str) -> str:
    """Remove transcription artifacts (e.g. ``[Speaker]``) and normalize whitespace."""
    if not text:
        return text
    cleaned = _SPEAKER_ARTIFACT_RE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _format_timestamp(ms: int) -> str:
    """Convert milliseconds to HH:MM:SS format."""
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_plain(result: dict) -> str:
    """Format the result as plain text with timestamps and speaker labels.

    Output format:
        [00:01:36] Speaker A: Hello everyone.
    Falls back to raw text when utterances are not available.
    """
    utterances = result.get("utterances")
    if utterances:
        lines = []
        for u in utterances:
            speaker = u.get("speaker", "Unknown")
            text = _clean_utterance_text(u.get("text", ""))
            timestamp = _format_timestamp(u.get("start", 0))
            lines.append(f"[{timestamp}] {speaker}: {text}")
        return "\n".join(lines)
    return _clean_utterance_text(result.get("text", ""))


def _format_segments(result: dict) -> list[dict]:
    """Format the result as a list of segment dicts matching TranscriptSegment shape.

    Prefers `utterances` (diarized with speaker labels, ms timestamps).
    Falls back to `words` aggregated, then a single segment from `text`.
    """
    utterances = result.get("utterances")
    if utterances:
        return [
            {
                "start": round(u.get("start", 0) / 1000, 2),
                "end": round(u.get("end", 0) / 1000, 2),
                "text": _clean_utterance_text(u.get("text", "")),
                "speaker": u.get("speaker"),
            }
            for u in utterances
        ]

    words = result.get("words")
    if words:
        segments: list[dict] = []
        current: dict | None = None
        for w in words:
            start = round(w.get("start", 0) / 1000, 2)
            end = round(w.get("end", 0) / 1000, 2)
            speaker = w.get("speaker")
            text = _clean_utterance_text(w.get("text", ""))

            if current and current.get("speaker") == speaker:
                current["end"] = end
                current["text"] = (current.get("text", "") + " " + text).strip()
            else:
                if current is not None:
                    segments.append(current)
                current = {
                    "start": start,
                    "end": end,
                    "text": text,
                    "speaker": speaker,
                }
        if current is not None:
            segments.append(current)
        return segments

    text = _clean_utterance_text(result.get("text", ""))
    if text:
        return [{"start": 0.0, "end": 0.0, "text": text}]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def transcribe_assemblyai(audio_path: Path) -> str:
    """Transcribe an audio file via AssemblyAI and return plain text.

    Pipeline: upload → submit → poll → format_plain.
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is required when asr.provider=assemblyai")

    upload_url = await _upload(audio_path)
    transcript_id = await _submit(upload_url)
    result = await _poll(transcript_id)
    return _format_plain(result)


async def transcribe_assemblyai_with_segments(audio_path: Path) -> list[dict]:
    """Transcribe and return segments with timestamps and optional speaker labels.

    Pipeline: upload → submit → poll → format_segments.
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is required when asr.provider=assemblyai")

    upload_url = await _upload(audio_path)
    transcript_id = await _submit(upload_url)
    result = await _poll(transcript_id)
    return _format_segments(result)

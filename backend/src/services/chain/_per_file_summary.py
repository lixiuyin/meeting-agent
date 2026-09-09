"""Per-file summary generation with kind-aware prompts."""

from ...core.config import settings
from .. import llm as llm_service
from ..tokenizer import count_tokens

_CHARS_PER_TOKEN = 3.5  # conservative estimate for mixed CJK/EN text

_AV_PROMPT = (
    "You are writing a **File Summary** for a single audio/video file within a meeting. "
    "This is NOT a meeting summary — only summarize what is in this specific file. "
    "Output structured Markdown with these sections:\n\n"
    "### File Summary\n"
    "**Overview**: 2-3 sentence overview of this file's content.\n"
    "**Topics Discussed**: bullet list of major topics.\n"
    "**Key Decisions**: bullet list with decision owner when identifiable.\n"
    "**Action Items**: | Action | Owner | Deadline | table.\n"
    "**Speaker Contributions**: brief 1-liner per speaker.\n"
    "**Key Timestamps Per Speaker**\n\n"
    "For EVERY identified speaker, output `**SpeakerName:**` on its own line, "
    "then list 3-6 highlights — each on its OWN line as a bullet: \n"
    "`- HH:MM:SS - note`. Example:\n\n"
    "**Alice:**\n"
    "- 00:01:23 - Raises concern about timeline.\n"
    "- 00:03:45 - Proposes alternative approach.\n\n"
    "**Bob:**\n"
    "- 00:02:10 - Agrees with the proposal.\n\n"
    "IMPORTANT: the section title and each speaker name MUST be on separate lines. "
    "Do NOT put multiple timestamps on a single line. "
    "Do NOT omit any speaker found in the transcript."
)

_PROMPTS: dict[str, str] = {
    "video": _AV_PROMPT,
    "audio": _AV_PROMPT,
    "pdf": (
        "You are writing a **File Summary** for a single document. "
        "This is NOT a meeting summary. "
        "Output structured Markdown with these sections:\n\n"
        "### File Summary\n"
        "**Purpose & Scope**: what this document covers and why.\n"
        "**Key Findings**: bullet list of main findings.\n"
        "**Critical Data/Numbers**: bullet list of dates, figures, metrics.\n"
        "**Clauses or Terms of Note**: bullet list of important clauses or terms.\n"
        "**Action Items**: | Action | Owner | Deadline | table."
    ),
    "ppt": (
        "You are writing a **File Summary** for a single presentation file. "
        "This is NOT a meeting summary. "
        "Output structured Markdown with these sections:\n\n"
        "### File Summary\n"
        "**Thesis / Main Message**: the central argument or message.\n"
        "**Section Outline**: bullet list (section title + 1-line key claim).\n"
        "**Key Decisions / Recommendations**: bullet list.\n"
        "**Action Items**: | Action | Owner | Deadline | table."
    ),
    "image": (
        "You are writing a **File Summary** for a single image. "
        "This is NOT a meeting summary. Describe what the image shows and "
        "its relevance to the meeting."
    ),
    "default": (
        "You are writing a **File Summary** for a single file. "
        "This is NOT a meeting summary. Summarize this file content with "
        "key points and action items from this file only."
    ),
}


def _truncate_to_token_budget(text: str, max_tokens: int, model: str) -> str:
    """Truncate text so it fits within max_tokens for the target model.

    Uses a fast char-based pre-filter, then refines with the model tokenizer.
    Preserves the head of the transcript and appends a marker when truncated.
    """
    if max_tokens <= 0 or not text:
        return text
    if count_tokens(text, model) <= max_tokens:
        return text
    char_budget = int(max_tokens * _CHARS_PER_TOKEN)
    candidate = text[:char_budget]
    while candidate and count_tokens(candidate, model) > max_tokens:
        candidate = candidate[: int(len(candidate) * 0.9)]
    return f"{candidate}\n\n[truncated]"


def _extract_key_points(summary: str, limit: int = 10) -> list[str]:
    points: list[str] = []
    for line in summary.splitlines():
        stripped = line.strip().lstrip("-*•").strip()
        if not stripped:
            continue
        if len(stripped) < 12:
            continue
        points.append(stripped)
        if len(points) >= limit:
            break
    return points


def _format_seconds(seconds: float) -> str:
    """Format seconds into HH:MM:SS string."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _render_speaker_timeline(segments: list[dict]) -> str:
    """Render a compact speaker timeline from structured segments.

    Samples up to ~80 lines per speaker to keep the prompt compact.
    Each segment is expected to have ``start``, ``end``, ``text``, and ``speaker``.
    """
    if not segments:
        return ""

    by_speaker: dict[str, list[str]] = {}
    for seg in segments:
        speaker = seg.get("speaker") or "Unknown"
        start = seg.get("start", 0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        entry = f"[{_format_seconds(start)}] {speaker}: {text}"
        by_speaker.setdefault(speaker, []).append(entry)

    lines: list[str] = ["### Speaker Timeline"]
    max_per_speaker = 80
    for speaker, entries in by_speaker.items():
        lines.append(f"\n**{speaker}**:")
        for entry in entries[:max_per_speaker]:
            lines.append(entry)
        if len(entries) > max_per_speaker:
            lines.append(f"... ({len(entries) - max_per_speaker} more utterances)")
    return "\n".join(lines)


async def generate_per_file_summary(
    *,
    file_type: str,
    file_name: str,
    text: str,
    segments: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """Generate per-file summary and key points."""
    llm = llm_service.get_llm()
    directive = _PROMPTS.get(file_type, _PROMPTS["default"])
    capped_text = _truncate_to_token_budget(
        text, settings.PER_FILE_SUMMARY_INPUT_MAX_TOKENS, settings.LLM_MODEL
    )

    # Build speaker timeline block for audio/video when segments are available
    timeline_block = ""
    if segments and file_type in ("video", "audio"):
        timeline_block = _render_speaker_timeline(segments)

    prompt = f"{directive}\n\nFile: {file_name}\n\n"
    if timeline_block:
        prompt += f"{timeline_block}\n\n"
    prompt += (
        "Follow the output structure specified above.\n\n"
        f"Content:\n{capped_text}\n\n### File Summary"
    )
    summary = await llm_service.invoke_llm_text(llm, prompt)
    return summary, _extract_key_points(summary)

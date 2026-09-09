"""Context formatting helpers for the RAG pipeline."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from ..llm._prompt_safety import escape_prompt_data
from ..rag._indexer import display_speakers

# Strip transcription artifacts (e.g. literal ``[Speaker]`` tokens that AssemblyAI
# occasionally emits) from chunk content before it is rendered to the LLM context
# or returned to the UI. Existing chunks indexed before the ASR/indexer cleanup
# was added still carry these tokens, so we scrub them at display time.
_SPEAKER_ARTIFACT_RE = re.compile(r"[^\S\n]*\[Speaker\][^\S\n]*", re.IGNORECASE)
_RETRIEVAL_CONTEXT_PREFIX_RE = re.compile(r"^\s*\[Retrieval context:[^\]]*\]\s*", re.IGNORECASE)


def _scrub_speaker_artifact(text: str) -> str:
    """Remove ``[Speaker]`` tokens and collapse the resulting whitespace."""
    if not text:
        return text
    cleaned = _SPEAKER_ARTIFACT_RE.sub(" ", text)
    return re.sub(r"[^\S\n]{2,}", " ", cleaned).strip()


def _scrub_display_content(text: str) -> str:
    """Remove index-only metadata before content reaches prompts or clients."""
    return _scrub_speaker_artifact(_RETRIEVAL_CONTEXT_PREFIX_RE.sub("", text, count=1))


def _extract_image_combined_caption(raw_content: str) -> str | None:
    """Parse the caption line from an image_combined chunk."""
    match = re.search(r"^\[Caption\] (.+?)(?:\n\[OCR\]|$)", raw_content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def _extract_image_combined_ocr(raw_content: str) -> str | None:
    """Parse the OCR line from an image_combined chunk."""
    match = re.search(r"\[OCR\]\s*(.+)", raw_content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _infer_source_kind(metadata: dict) -> str:
    """Infer the source citation kind from chunk metadata.

    Returns one of: "timestamp", "slide", "page", "image", "text",
    "meeting_summary", "file_summary".
    """
    existing = metadata.get("source_kind")
    if existing in {
        "timestamp",
        "slide",
        "page",
        "image",
        "text",
        "meeting_summary",
        "file_summary",
    }:
        return existing

    if metadata.get("timestamp_start") is not None:
        return "timestamp"

    # PPT/PPTX converted to PDF — preserve "slide" citation semantics.
    original_format = metadata.get("original_format", "")
    if original_format in ("ppt", "pptx"):
        return "slide"

    file_type = metadata.get("file_type", "")
    if file_type in ("video", "audio"):
        return "timestamp"
    if file_type in ("ppt", "pptx"):
        return "slide"
    if file_type == "image":
        return "image"
    # PDF and others that have page_number
    if metadata.get("page_number") is not None:
        return "page"
    return "text"


def _format_docs(docs: list[dict]) -> str:
    """Format retrieved documents into compact context with numbered source headers."""
    parts = []
    for i, doc in enumerate(docs, 1):
        meta = doc.get("metadata", {})
        meeting_id = meta.get("meeting_id", "?")
        file_name = meta.get("file_name")
        page = meta.get("page_number")
        ts_start = meta.get("timestamp_start")
        ts_end = meta.get("timestamp_end")
        speaker = meta.get("speaker")
        source_kind = _infer_source_kind(meta)
        evidence_labels = "; ".join(
            label
            for label in (
                f"role={meta['material_role']}" if meta.get("material_role") else "",
                f"approval={meta['approval_status']}" if meta.get("approval_status") else "",
            )
            if label
        )

        if source_kind == "meeting_summary":
            title = meta.get("meeting_title") or meta.get("title") or f"Meeting#{meeting_id}"
            ref = f"Summary: {title} (Meeting #{meeting_id})"
        elif source_kind == "file_summary":
            ref = f"File Summary: {file_name or 'unknown'} (Meeting #{meeting_id})"
        else:
            # Compact reference: Meet#123 · file.pdf · p5
            ref = f"Meet#{meeting_id}"
            if file_name:
                ref += f" · {file_name}"
            if source_kind == "timestamp" and ts_start is not None and ts_end is not None:
                ref += f" · {ts_start:.0f}s-{ts_end:.0f}s"
                speakers_in_chunk = meta.get("speakers_in_chunk", "")
                if speaker:
                    ref += f" ({speaker})"
                elif speakers_in_chunk and "\x1f" in speakers_in_chunk:
                    ref += f" ({display_speakers(speakers_in_chunk)})"
            elif source_kind == "slide" and page is not None:
                ref += f" · slide{page}"
            elif source_kind == "page" and page is not None:
                ref += f" · p{page}"
            elif source_kind == "image":
                ref += " · img"
        if evidence_labels:
            ref += f" · {evidence_labels}"

        parts.append(f"[{i}] {ref}\n{_scrub_display_content(doc['content'])}")
    return "\n\n".join(parts) if parts else "No relevant meeting content found."


def _format_docs_by_meeting(
    docs: list[dict],
    speaker_names: list[str],
    meeting_titles: dict[int, str] | None = None,
) -> str:
    """Format docs grouped by meeting, with speaker emphasis.

    Used when the query targets a specific speaker and results span
    multiple meetings.  The output makes clear which meeting each piece
    of evidence comes from, enabling the LLM to attribute per-meeting.

    Args:
        meeting_titles: mapping of meeting_id → human-readable meeting title.
            Falls back to metadata ``title`` (often file name) if not provided.
    """
    titles = meeting_titles or {}

    groups: defaultdict[int, list[tuple[int, dict]]] = defaultdict(list)
    for i, doc in enumerate(docs, 1):
        meta = doc.get("metadata", {})
        mid = meta.get("meeting_id", 0)
        groups[mid].append((i, doc))

    speakers_str = ", ".join(speaker_names)
    parts: list[str] = []

    for mid, items in groups.items():
        display_title = titles.get(mid) or f"Meeting#{mid}"
        section = f"## Meeting: {display_title}\n"
        section += f"(Queried speaker: {speakers_str})\n\n"
        for idx, doc in items:
            meta = doc.get("metadata", {})
            file_name = meta.get("file_name")
            ts_start = meta.get("timestamp_start")
            ts_end = meta.get("timestamp_end")
            speaker = meta.get("speaker")
            source_kind = _infer_source_kind(meta)
            evidence_labels = "; ".join(
                label
                for label in (
                    f"role={meta['material_role']}" if meta.get("material_role") else "",
                    f"approval={meta['approval_status']}" if meta.get("approval_status") else "",
                )
                if label
            )

            if source_kind == "meeting_summary":
                ref = f"Summary: {display_title}"
            elif source_kind == "file_summary":
                ref = f"File Summary: {display_title} / {file_name or 'unknown'}"
            else:
                ref = display_title
                if file_name:
                    ref = f"{display_title} / {file_name}"
                if source_kind == "timestamp" and ts_start is not None and ts_end is not None:
                    ref = f"{ref} [{ts_start:.1f}s-{ts_end:.1f}s]"
                    speakers_in_chunk = meta.get("speakers_in_chunk", "")
                    if speakers_in_chunk and "\x1f" in speakers_in_chunk:
                        ref = f"{ref} (speakers: {display_speakers(speakers_in_chunk)})"
                    elif speaker:
                        ref = f"{ref} ({speaker})"
                elif source_kind == "slide" and meta.get("page_number") is not None:
                    ref = f"{ref} Slide {meta['page_number']}"
                elif source_kind == "page" and meta.get("page_number") is not None:
                    ref = f"{ref} p.{meta['page_number']}"

            if evidence_labels:
                ref += f" · {evidence_labels}"

            section += f"**[{idx}] Source: {ref}**\n{_scrub_display_content(doc['content'])}\n\n"
        parts.append(section.strip())

    return "\n\n---\n\n".join(parts) if parts else "No relevant meeting content found."


def _canonical_citation_docs(docs: list[dict]) -> list[dict]:
    """Return docs in the same order/identity used by ``_extract_sources``.

    Keeps ``_format_docs`` (LLM-visible [N]) aligned with the frontend
    ``sources`` array.  Mirrors the skip/dedupe logic in ``_extract_sources``:
    drops docs without ``meeting_id``, deduplicates by
    ``source_kind:meeting_id:file_id:chunk_index``.
    """
    seen: set[str] = set()
    kept: list[dict] = []
    for doc in docs:
        meta = doc.get("metadata") or {}
        meeting_id = meta.get("meeting_id")
        if meeting_id is None:
            continue
        source_kind = _infer_source_kind(meta)
        key = f"{source_kind}:{meeting_id}:{meta.get('file_id')}:{meta.get('chunk_index')}"
        if key in seen:
            continue
        seen.add(key)
        kept.append(doc)
    return kept


def _extract_sources(
    docs: list[dict], max_sources: int | None = None, *, memory_sources: list[dict] | None = None
) -> list[dict]:
    """Extract source metadata for response, deduplicating by chunk (meeting:file:chunk).

    Returns at most ``max_sources`` items so the UI can render a stable top-N citations list.
    Pass ``None`` for no limit.
    """
    sources = []
    seen: set[str] = set()
    for doc in docs:
        meta = doc.get("metadata", {})
        meeting_id = meta.get("meeting_id")
        file_id = meta.get("file_id")
        chunk_index = meta.get("chunk_index")
        page = meta.get("page_number")
        # Namespace dedup key by source_kind to avoid collisions between
        # chunk docs and synthetic summary docs (which may share meeting_id / file_id).
        source_kind = _infer_source_kind(meta)
        chunk_key = f"{source_kind}:{meeting_id}:{file_id}:{chunk_index}"
        if meeting_id is None or chunk_key in seen:
            continue
        seen.add(chunk_key)
        raw_content = _scrub_display_content(str(doc.get("content", "") or "").strip())
        chunk_id = str(meta["chunk_id"]) if meta.get("chunk_id") is not None else None
        source_id = chunk_id
        if source_kind in {"file_summary", "meeting_summary"}:
            # A derived summary has its own content version, never a fabricated
            # original chunk ID. Preserve the identity of a restored snapshot.
            identity = f"{source_kind}:{meeting_id}:{file_id}:{raw_content}"
            source_id = meta.get("source_id") or (
                f"{source_kind}:{hashlib.sha256(identity.encode()).hexdigest()}"
            )
        # Avoid duplicating the complete retrieved corpus in an SSE metadata
        # event. The authenticated file endpoints remain the source of truth.
        source_preview = raw_content[:4000]
        if len(raw_content) > len(source_preview):
            source_preview = source_preview.rstrip() + "…"
        slide_number = page if source_kind == "slide" and page is not None else None
        content_type = meta.get("content_type")
        heading_path = meta.get("heading_path")
        image_path = meta.get("image_storage_path")
        image_thumbnail_path = meta.get("image_thumbnail_path")
        page_image_path = meta.get("page_image_storage_path")
        page_image_thumbnail_path = meta.get("page_image_thumbnail_path")
        sources.append(
            {
                "meeting_id": meeting_id,
                "meeting_title": meta.get("title", f"Meeting#{meeting_id}"),
                # Native ingestion publishes immutable shadow generations.
                # Expose that generation as the citation revision unless a
                # parser supplied a more specific document revision.
                "document_revision": (
                    str(meta.get("document_revision") or meta.get("index_generation"))
                    if meta.get("document_revision") is not None
                    or meta.get("index_generation") is not None
                    else None
                ),
                "alternate_sources": meta.get("alternate_sources") or [],
                # Keep source as original chunk text (not model summary) for verbatim citation.
                "content": source_preview,
                "score": round(doc.get("score", 0), 4),
                "file_id": file_id,
                "file_name": meta.get("file_name"),
                "chunk_id": chunk_id,
                "source_id": source_id,
                "window_start": meta.get("evidence_start_offset", meta.get("window_start")),
                "window_end": meta.get("evidence_end_offset", meta.get("window_end")),
                "chunk_index": chunk_index,
                "page_number": meta.get("page_number"),
                "slide_number": slide_number,
                "timestamp_start": meta.get("timestamp_start"),
                "timestamp_end": meta.get("timestamp_end"),
                "speaker": meta.get("speaker"),
                "speakers_in_chunk": meta.get("speakers_in_chunk"),
                "time_position_ratio": meta.get("time_position_ratio"),
                "meeting_duration": meta.get("meeting_duration"),
                "file_type": meta.get("file_type"),
                "source_kind": source_kind,
                "content_type": content_type if isinstance(content_type, str) else None,
                "image_caption": (
                    source_preview
                    if content_type == "image_caption"
                    else _extract_image_combined_caption(raw_content)
                    if content_type == "image_combined"
                    else None
                ),
                "image_ocr": (
                    source_preview
                    if content_type == "image_ocr"
                    else _extract_image_combined_ocr(raw_content)
                    if content_type == "image_combined"
                    else None
                ),
                "table_markdown": source_preview if content_type == "table" else None,
                "image_path": image_path if isinstance(image_path, str) else None,
                "image_thumbnail_path": (
                    image_thumbnail_path if isinstance(image_thumbnail_path, str) else None
                ),
                "page_image_path": page_image_path if isinstance(page_image_path, str) else None,
                "page_image_thumbnail_path": (
                    page_image_thumbnail_path
                    if isinstance(page_image_thumbnail_path, str)
                    else None
                ),
                "heading_path": (
                    [str(x) for x in heading_path]
                    if isinstance(heading_path, (list, tuple))
                    else []
                ),
                "confidence": meta.get("confidence"),
            }
        )
        if max_sources is not None and len(sources) >= max_sources:
            break
    return sources + (memory_sources or [])


def _build_system_context(
    memory_context: str,
    session_context: str,
    entity_context: str,
    meeting_context: str,
    web_context: str,
    file_summaries_context: str = "",
    meeting_summaries_context: str = "",
) -> str:
    """Build combined system context from multiple sources with explicit source tags.

    Entity context is merged into the memory section as entity.* key=val lines
    to save a section header and improve contextual cohesion.
    """
    parts = []
    if memory_context or entity_context:
        combined_memory = memory_context
        if entity_context:
            if combined_memory:
                combined_memory = f"{combined_memory}\n{entity_context}"
            else:
                combined_memory = entity_context
        parts.append(f"<user_memory>\n{escape_prompt_data(combined_memory)}\n</user_memory>")
    if session_context:
        parts.append(
            f"<prior_conversations>\n{escape_prompt_data(session_context)}\n</prior_conversations>"
        )
    if web_context:
        parts.append(f"<web_search>\n{escape_prompt_data(web_context)}\n</web_search>")
    if meeting_summaries_context:
        parts.append(
            f"<meeting_summaries>\n{escape_prompt_data(meeting_summaries_context)}\n"
            "</meeting_summaries>"
        )
    if file_summaries_context:
        parts.append(
            f"<file_summaries>\n{escape_prompt_data(file_summaries_context)}\n</file_summaries>"
        )
    if meeting_context and meeting_context != "No relevant meeting content found.":
        parts.append(f"[Meeting Content]\n{escape_prompt_data(meeting_context)}")
    return "\n\n".join(parts) if parts else "No context available."


_VISUAL_QUERY_PATTERNS = (
    # English keywords that signal the user wants to reason about pictures.
    re.compile(
        r"\b(image|images|picture|pictures|photo|photos|figure|figures|diagram|diagrams|"
        r"chart|charts|graph|graphs|screenshot|screenshots|screencap|map|maps|"
        r"slide|slides|visual|visually|see|show|depict|illustrated?)\b",
        re.IGNORECASE,
    ),
    # Common CJK requests for visual inspection.
    re.compile(r"图|图片|截图|画面|示意图|图表|图形|图像|看|展示|幻灯片"),
)


def is_visual_query(query: str) -> bool:
    """Heuristic: does the user actually want the model to look at images?

    Attaching images to the LLM call costs a lot of tokens (base64 payload +
    vision features). For text-only questions that happen to hit image chunks
    in retrieval, the answer rarely benefits from the image data itself.
    """
    if not query:
        return False
    return any(p.search(query) for p in _VISUAL_QUERY_PATTERNS)


def extract_image_urls_from_docs(
    docs: list[dict],
    max_images: int = 5,
) -> list[dict[str, str | int]]:
    """Extract resolvable image URLs from retrieved documents.

    Returns a list of dicts with keys:
    - ``url``: the resolved URL for the image (to be used as image_url content)
    - ``source_index``: 1-based index matching the formatted context citation number
    - ``caption``: image caption if available

    Images are prioritized by: direct image chunks > page images > thumbnails.
    """
    images: list[dict[str, str | int]] = []
    seen_paths: set[str] = set()

    # Build the public asset URL base for the LLM to fetch images.
    # The LLM needs an HTTP-accessible URL — use the backend's own asset endpoint.
    # For local providers (ollama etc.), the LLM server can reach localhost:8000.
    # For cloud providers, the images need to be publicly accessible or we use base64.
    # We'll use base64 encoding for reliability across all providers.
    for i, doc in enumerate(docs):
        if len(images) >= max_images:
            break
        meta = doc.get("metadata", {})
        content_type = meta.get("content_type", "")

        # Extract the most relevant image path
        image_path = (
            meta.get("image_storage_path")
            or meta.get("page_image_storage_path")
            or meta.get("image_thumbnail_path")
            or meta.get("page_image_thumbnail_path")
        )
        if not isinstance(image_path, str) or image_path in seen_paths:
            continue

        # Only include image-type chunks and page images
        if content_type not in (
            "image_caption",
            "image_ocr",
            "image_combined",
            "table",
            "text",
            "",
            None,
        ):
            continue

        seen_paths.add(image_path)
        caption = ""
        raw_content = str(doc.get("content", "")).strip()
        if content_type == "image_combined":
            caption_match = _extract_image_combined_caption(raw_content)
            if caption_match:
                caption = caption_match
        elif content_type == "image_caption":
            caption = raw_content[:200]

        images.append(
            {
                "storage_path": image_path,
                "source_index": i + 1,
                "caption": caption,
            }
        )

    return images


def load_image_as_base64_url(storage_path: str) -> str | None:
    """Load an image from storage and return as a data URI (base64-encoded).

    Returns None if the file doesn't exist or can't be read.
    """
    import base64
    import mimetypes
    from pathlib import Path

    from ...core.constants import UPLOAD_DIR

    full_path = Path(UPLOAD_DIR) / storage_path
    if not full_path.exists():
        return None

    mime_type, _ = mimetypes.guess_type(str(full_path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"

    try:
        data = full_path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{b64}"
    except OSError:
        return None

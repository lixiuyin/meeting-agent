"""Document indexing: chunking + storage in Chroma and FTS5."""

import logging
import math
import re as _re
import threading
from typing import Any

from cachetools import LRUCache
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ...core.config import settings
from ...core.trace import TraceContext
from ..embedder import get_embeddings
from ..llm._embeddings_adapter import embed_documents_batched
from ..parser.types import ParsedDocument
from ..tokenizer import count_tokens
from ._chunkers import _split_by_structure
from ._indexer_extract import (
    _extract_image_texts,
    _normalize_table_markdown,
    _page_extra_meta,
    _page_preview_paths,
)
from ._indexer_store import (
    _add_docs_to_bm25,
    _add_to_bm25,
    _chunk_id_prefix,
    _dedup_existing_chunks,
    _upsert_with_trace,
    delete_meeting_chunks,
)
from ._vectorstore import get_vectorstore

logger = logging.getLogger(__name__)

# Per-file reindex locks prevent concurrent delete+upsert interleaving
# for the same (meeting_id, file_id) pair (R-H3).
# RLock is required because _speakers.py acquires the lock externally
# and then delete_meeting_chunks acquires the same lock internally.
_file_reindex_locks: LRUCache[tuple[int, int], threading.RLock] = LRUCache(maxsize=8192)
_file_reindex_locks_lock = threading.Lock()


def _acquire_file_reindex_lock(meeting_id: int, file_id: int) -> threading.RLock:
    """Obtain (or create) a threading.RLock for the given file reindex scope."""
    key = (meeting_id, file_id)
    with _file_reindex_locks_lock:
        lock = _file_reindex_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _file_reindex_locks[key] = lock
        return lock


# Speaker delimiter for speakers_in_chunk metadata.
# Uses ASCII Unit Separator (\x1f) to avoid collision with commas in speaker names.
_SPEAKER_DELIMITER = "\x1f"

# Strip transcription artifacts (e.g. literal "[Speaker]" tokens that AssemblyAI
# occasionally emits in utterance text) before chunking. This is a safety net for
# segments that were transcribed before the ASR-layer cleanup was added.
_SPEAKER_ARTIFACT_RE = _re.compile(r"\s*\[Speaker\]\s*", _re.IGNORECASE)


def _scrub_segment_text(text: str) -> str:
    """Remove ``[Speaker]`` artifacts and collapse the resulting whitespace."""
    if not text:
        return text
    cleaned = _SPEAKER_ARTIFACT_RE.sub(" ", text)
    return _re.sub(r"\s{2,}", " ", cleaned).strip()


def split_speakers(raw: str) -> list[str]:
    """Split speakers_in_chunk, handling both new (\\x1f) and legacy comma delimiters."""
    if not raw:
        return []
    return [s.strip() for s in _re.split(r"[,\x1f]", raw) if s.strip()]


def display_speakers(raw: str) -> str:
    """Format speakers_in_chunk for human display."""
    return raw.replace(_SPEAKER_DELIMITER, ", ")


# Common separators for text splitting.
# M-16: CJK punctuation included so Chinese/Japanese/Korean text splits at
# natural sentence and clause boundaries instead of being treated as one blob.
_SEPARATORS = [
    "\n\n---\n\n",  # horizontal rule (e.g. section dividers)
    "\n\n",  # paragraph break
    "\n",  # line break
    "。",  # Chinese/Japanese period
    "！",  # noqa: RUF001  # Chinese/Japanese exclamation (M-16)
    "？",  # noqa: RUF001  # Chinese/Japanese question mark (M-16)
    "；",  # noqa: RUF001  # Chinese/Japanese semicolon
    ". ",  # English sentence end
    "! ",  # English exclamation
    "? ",  # English question
    " ",  # word boundary
    "",
]


def _token_splitter(*, child: bool = False) -> RecursiveCharacterTextSplitter:
    token_size = getattr(
        settings, "CHILD_CHUNK_SIZE_TOKENS" if child else "CHUNK_SIZE_TOKENS", None
    )
    token_overlap = getattr(
        settings,
        "CHILD_CHUNK_OVERLAP_TOKENS" if child else "CHUNK_OVERLAP_TOKENS",
        None,
    )
    if not isinstance(token_size, int):
        token_size = int(settings.CHILD_CHUNK_SIZE if child else settings.CHUNK_SIZE)
    if not isinstance(token_overlap, int):
        token_overlap = int(settings.CHILD_CHUNK_OVERLAP if child else settings.CHUNK_OVERLAP)
    return RecursiveCharacterTextSplitter(
        chunk_size=token_size,
        chunk_overlap=token_overlap,
        length_function=count_tokens,
        separators=_SEPARATORS,
    )


def index_meeting(
    meeting_id: int,
    text: str,
    metadata: dict,
    trace: TraceContext | None = None,
    target_vs: Any = None,
    skip_bm25: bool = False,
    strict_bm25: bool = False,
) -> None:
    """Chunk meeting text and store in vector DB.

    Args:
        target_vs: Optional Chroma vectorstore to write to instead of the singleton.
                   Used by the shadow-collection rebuild (C-4) to index into a staging
                   collection without disturbing the live one.
        skip_bm25: When True, skip the BM25/FTS5 write.  The caller is responsible
                   for rebuilding BM25 after an atomic swap.
    """
    if settings.PARENT_CHILD_ENABLED:
        _index_parent_child(meeting_id, text, metadata, _SEPARATORS, trace, target_vs)
    else:
        _index_flat(meeting_id, text, metadata, _SEPARATORS, trace, target_vs)

    if not skip_bm25:
        _add_to_bm25(meeting_id, text, metadata, _SEPARATORS, strict=strict_bm25)


def index_meeting_pages(
    meeting_id: int,
    parsed: ParsedDocument,
    metadata: dict,
    trace: TraceContext | None = None,
    replace_existing: bool = True,
    strict_bm25: bool = False,
) -> list[tuple[str, int, str, dict[str, Any]]] | None:
    """Index a parsed document preserving page/slide numbers in chunk metadata."""
    if trace:
        trace.start_span("chunk", "index")

    try:
        splitter = _token_splitter()

        all_chunks: list[tuple[str, int, str, dict[str, Any]]] = []
        for page in parsed.pages:
            page_num = page.page_num
            page_text = page.text or ""
            page_extra_meta = _page_extra_meta(page)
            page_image_path, page_image_thumbnail_path = _page_preview_paths(page)
            if page_image_path:
                page_extra_meta["page_image_storage_path"] = page_image_path
            if page_image_thumbnail_path:
                page_extra_meta["page_image_thumbnail_path"] = page_image_thumbnail_path
            if page_text.strip():
                token_size = getattr(settings, "CHUNK_SIZE_TOKENS", None)
                if not isinstance(token_size, int):
                    token_size = int(settings.CHUNK_SIZE)
                if count_tokens(page_text) <= token_size:
                    all_chunks.append((page_text, page_num, "text", page_extra_meta))
                else:
                    for sub in splitter.split_text(page_text):
                        all_chunks.append((sub, page_num, "text", page_extra_meta))

            if settings.RAG_INDEX_TABLES:
                legacy_tables = page.tables or []
                modern_tables = list(page.table_assets or ())
                for table in [*modern_tables, *legacy_tables]:
                    table_text = _normalize_table_markdown(table)
                    if table_text:
                        all_chunks.append((table_text, page_num, "table", page_extra_meta))

            if settings.RAG_INDEX_IMAGE_CAPTIONS:
                images = [*(page.image_assets or ()), *(page.images or [])]
                for _image_index, image_payload in enumerate(images):
                    caption, ocr_text, storage_path, thumbnail_path = _extract_image_texts(
                        image_payload
                    )
                    image_meta = {}
                    if storage_path:
                        image_meta["image_storage_path"] = storage_path
                    if thumbnail_path:
                        image_meta["image_thumbnail_path"] = thumbnail_path
                    image_meta.update(page_extra_meta)

                    has_real_content = bool(caption) or (
                        ocr_text and len(ocr_text) >= settings.RAG_IMAGE_OCR_MIN_LENGTH
                    )
                    if not has_real_content:
                        continue

                    if caption and ocr_text and len(ocr_text) >= settings.RAG_IMAGE_OCR_MIN_LENGTH:
                        combined = f"[Caption] {caption}\n[OCR] {ocr_text}"
                        all_chunks.append((combined, page_num, "image_combined", image_meta))
                    elif caption:
                        all_chunks.append((caption, page_num, "image_caption", image_meta))
                    elif ocr_text and len(ocr_text) >= settings.RAG_IMAGE_OCR_MIN_LENGTH:
                        all_chunks.append((ocr_text, page_num, "image_ocr", image_meta))
    finally:
        if trace:
            trace.finish_span("chunk")

    if not all_chunks:
        logger.warning("Meeting %d: empty parsed document, skipping index", meeting_id)
        return None

    file_id = metadata.get("file_id")
    reindex_lock = _acquire_file_reindex_lock(meeting_id, file_id) if file_id is not None else None
    if reindex_lock is not None:
        reindex_lock.acquire()
    try:
        if file_id is not None and replace_existing:
            delete_meeting_chunks(meeting_id, file_id=file_id)

        docs = [
            Document(
                page_content=text,
                metadata={
                    "meeting_id": meeting_id,
                    "chunk_index": i,
                    "page_number": page_num,
                    "content_type": content_type,
                    **extra_meta,
                    **metadata,
                },
            )
            for i, (text, page_num, content_type, extra_meta) in enumerate(all_chunks)
        ]
        prefix = _chunk_id_prefix(
            meeting_id,
            metadata.get("file_id"),
            metadata.get("source_kind"),
            metadata.get("index_generation"),
        )
        ids = [f"{prefix}_chunk_{i}" for i in range(len(docs))]
        logical_prefix = _chunk_id_prefix(
            meeting_id,
            metadata.get("file_id"),
            metadata.get("source_kind"),
        )
        for i, doc in enumerate(docs):
            doc.metadata["chunk_id"] = ids[i]
            doc.metadata["logical_chunk_id"] = f"{logical_prefix}_chunk_{i}"

        vectorstore = get_vectorstore()
        new_docs, new_ids = _dedup_existing_chunks(docs, ids, vectorstore)
        if not new_docs:
            logger.info("Meeting %d: all %d page chunks unchanged", meeting_id, len(docs))
            return all_chunks

        _upsert_with_trace(vectorstore, new_docs, new_ids, trace, meeting_id)

        _add_docs_to_bm25(meeting_id, docs, ids, strict=strict_bm25)

        return all_chunks
    finally:
        if reindex_lock is not None:
            reindex_lock.release()


def _collect_chunk_speakers(segments: list[dict], start: float, end: float) -> list[str]:
    """Collect unique speaker names that appear in segments within [start, end]."""
    speakers: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        sp = seg.get("speaker")
        if not sp or sp in seen:
            continue
        seg_start = seg.get("start", 0.0)
        seg_end = seg.get("end", seg_start)
        if seg_start >= end:
            break
        if seg_end > start:
            seen.add(sp)
            speakers.append(sp)
    return speakers


def _compute_cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def _detect_semantic_boundaries(
    embeddings: list[list[float]],
    threshold: float,
    min_segments: int,
    max_segments: int,
) -> list[int]:
    """Detect semantic boundaries between segments based on embedding similarity.

    Args:
        embeddings: List of embedding vectors for each segment
        threshold: Cosine similarity threshold below which to mark a boundary
        min_segments: Minimum segments per chunk (prevents over-fragmentation)
        max_segments: Maximum segments per chunk (prevents oversized chunks)

    Returns:
        List of indices where boundaries should be placed (split after this index)
    """
    boundaries = []
    segment_count = 0

    for i in range(len(embeddings) - 1):
        segment_count += 1

        # Force boundary at max segments
        if segment_count >= max_segments:
            boundaries.append(i)
            segment_count = 0
            continue

        # Compute similarity between adjacent segments
        sim = _compute_cosine_similarity(embeddings[i], embeddings[i + 1])

        # Mark boundary if similarity drops below threshold
        if sim < threshold and segment_count >= min_segments:
            boundaries.append(i)
            segment_count = 0

    return boundaries


def _format_segment_with_speaker(seg: dict) -> str:
    """Format a segment with speaker label prefix."""
    speaker = seg.get("speaker") or "Unknown"
    text = seg.get("text", "").strip()
    return f"{speaker}: {text}"


def _average_embeddings(embeddings: list[list[float]]) -> list[float]:
    """Compute element-wise average of embeddings."""
    if not embeddings:
        return []
    dim = len(embeddings[0])
    result = [0.0] * dim
    for emb in embeddings:
        for i, val in enumerate(emb):
            result[i] += val
    for i in range(dim):
        result[i] /= len(embeddings)
    return result


def _build_chunk_with_speaker_alignment(
    segments: list[dict],
    segment_indices: list[int],
    segment_embeddings: list[list[float]],
    last_speaker: str | None,
    include_speaker_in_content: bool,
) -> tuple[dict, str | None]:
    """Build a chunk from segments with speaker alignment.

    When a single long segment spans multiple chunks:
    - First chunk uses the segment's speaker
    - Subsequent chunks inherit the previous chunk's speaker

    Args:
        segments: Original segments list
        segment_indices: Indices of segments to include in this chunk
        segment_embeddings: Pre-computed embeddings for each segment
        last_speaker: Speaker from previous chunk (for alignment)
        include_speaker_in_content: Whether to prefix text with speaker labels

    Returns:
        Tuple of (chunk_dict, current_speaker)
    """
    first_idx = segment_indices[0]
    first_seg = segments[first_idx]

    # Determine dominant speaker by total character count across all segments in chunk.
    # Falls back to first segment's speaker or inherited speaker from previous chunk.
    speaker_lengths: dict[str, int] = {}
    for idx in segment_indices:
        sp = segments[idx].get("speaker")
        if sp:
            speaker_lengths[sp] = speaker_lengths.get(sp, 0) + len(segments[idx].get("text", ""))
    if speaker_lengths:
        current_speaker = max(speaker_lengths, key=lambda k: speaker_lengths[k])
    else:
        current_speaker = first_seg.get("speaker") or last_speaker or "Unknown"

    # Detect multi-speaker chunks to force speaker prefixes
    unique_speakers = set(speaker_lengths.keys()) if speaker_lengths else {current_speaker}
    force_speaker_prefix = len(unique_speakers) > 1

    # Build formatted text
    lines = []
    for idx in segment_indices:
        seg = segments[idx]
        seg_speaker = seg.get("speaker") or current_speaker
        seg_text = seg.get("text", "").strip()
        if seg_text:
            if include_speaker_in_content or force_speaker_prefix:
                lines.append(f"{seg_speaker}: {seg_text}")
            else:
                lines.append(seg_text)

    chunk_text = "\n\n".join(lines)

    # Compute chunk embedding as average of segment embeddings
    chunk_embedding = _average_embeddings([segment_embeddings[i] for i in segment_indices])

    # Timestamps
    start_ts = first_seg.get("start", 0.0)
    last_idx = segment_indices[-1]
    end_ts = segments[last_idx].get("end", 0.0)

    return {
        "text": chunk_text,
        "embedding": chunk_embedding,
        "timestamp_start": start_ts,
        "timestamp_end": end_ts,
        "speaker": current_speaker,
        "multi_speaker": force_speaker_prefix,
    }, current_speaker


def _group_segments_by_boundaries(
    segments: list[dict],
    segment_embeddings: list[list[float]],
    boundaries: set[int],
    max_chunk_size: int,
    include_speaker_in_content: bool,
    split_on_speaker_change: bool = True,
) -> list[dict]:
    """Group segments into chunks based on semantic boundaries, size, and speaker changes.

    Args:
        segments: Original segments list
        segment_embeddings: Pre-computed embeddings for each segment
        boundaries: Set of indices where semantic boundaries occur
        max_chunk_size: Maximum characters per chunk
        include_speaker_in_content: Whether to prefix text with speaker labels
        split_on_speaker_change: Whether to split chunks when speaker changes

    Returns:
        List of chunk dictionaries
    """
    chunks = []
    current_group: list[int] = []
    current_len = 0
    last_speaker: str | None = None
    current_group_speaker: str | None = None

    for i, seg in enumerate(segments):
        seg_len = len(seg.get("text", ""))
        seg_speaker = seg.get("speaker")

        should_split = False

        # 1. Semantic boundary
        if i in boundaries:
            should_split = True

        # 2. Character size limit
        if current_len + seg_len > max_chunk_size and current_group:
            should_split = True

        # 3. Speaker change
        if (
            split_on_speaker_change
            and current_group
            and seg_speaker
            and current_group_speaker
            and seg_speaker != current_group_speaker
        ):
            should_split = True

        if should_split and current_group:
            chunk, last_speaker = _build_chunk_with_speaker_alignment(
                segments,
                current_group,
                segment_embeddings,
                last_speaker,
                include_speaker_in_content,
            )
            chunks.append(chunk)
            current_group = []
            current_len = 0
            current_group_speaker = None

        current_group.append(i)
        current_len += seg_len
        if not current_group_speaker and seg_speaker:
            current_group_speaker = seg_speaker

    # Flush remaining
    if current_group:
        chunk, _ = _build_chunk_with_speaker_alignment(
            segments, current_group, segment_embeddings, last_speaker, include_speaker_in_content
        )
        chunks.append(chunk)

    return chunks


def index_meeting_segments(
    meeting_id: int,
    segments: list[dict],
    metadata: dict,
    trace: TraceContext | None = None,
    target_vs: Any = None,
    skip_bm25: bool = False,
    strict_bm25: bool = False,
) -> None:
    """Index audio/video transcription segments with semantic boundary detection.

    Enhanced version that:
    1. Embeds each segment and detects semantic boundaries via similarity drops
    2. Includes speaker labels in chunk content for better retrieval
    3. Aligns speaker labels across chunk boundaries for long segments
    4. Reuses computed embeddings for vector storage (no recomputation)

    Each chunk's metadata includes:
      - speaker: primary speaker (first speaker in chunk)
      - speakers_in_chunk: comma-separated list of all speakers in chunk
      - time_position_ratio: chunk midpoint as fraction of total meeting
        duration [0.0, 1.0], enabling temporal queries like "what did X
        say in the second half"

    Args:
        target_vs: Optional Chroma vectorstore to write to instead of the singleton.
                   Used by guarded whole-collection maintenance rebuilds.
        skip_bm25: When True, skip the BM25/FTS5 write.  The caller is responsible
                   for rebuilding BM25 after an atomic swap.
    """
    if trace:
        trace.start_span("chunk", "index")

    meeting_duration = 0.0
    if segments:
        meeting_duration = max(seg.get("end", seg.get("start", 0.0)) for seg in segments)

    try:
        # 1. Filter empty segments and strip transcription artifacts
        valid_segments = []
        for s in segments:
            cleaned = _scrub_segment_text(s.get("text", ""))
            if cleaned:
                valid_segments.append({**s, "text": cleaned})
        if not valid_segments:
            return

        # 2. Extract text with optional speaker labels
        include_speaker = settings.AUDIO_SPEAKER_IN_CONTENT
        texts = [
            _format_segment_with_speaker(s) if include_speaker else s.get("text", "").strip()
            for s in valid_segments
        ]

        # 3. Batch compute embeddings
        embeddings_fn = get_embeddings()
        segment_embeddings = embed_documents_batched(embeddings_fn, texts, batch_size=64)

        # 4. Detect semantic boundaries
        if settings.AUDIO_SEMANTIC_BOUNDARY_ENABLED:
            boundaries_set = set(
                _detect_semantic_boundaries(
                    segment_embeddings,
                    threshold=settings.AUDIO_SEMANTIC_BOUNDARY_THRESHOLD,
                    min_segments=settings.AUDIO_SEMANTIC_MIN_SEGMENTS,
                    max_segments=settings.AUDIO_SEMANTIC_MAX_SEGMENTS,
                )
            )
        else:
            boundaries_set = set()

        # 5. Group segments into chunks
        chunks = _group_segments_by_boundaries(
            valid_segments,
            segment_embeddings,
            boundaries_set,
            settings.CHUNK_SIZE,
            include_speaker,
            split_on_speaker_change=settings.AUDIO_SPLIT_ON_SPEAKER_CHANGE,
        )
    finally:
        if trace:
            trace.finish_span("chunk")

    if not chunks:
        logger.warning("Meeting %d: empty segments, skipping index", meeting_id)
        return

    # 6. Create documents with enriched metadata
    docs = []
    for i, chunk in enumerate(chunks):
        ts_start = chunk["timestamp_start"]
        ts_end = chunk["timestamp_end"]
        chunk_speakers = _collect_chunk_speakers(valid_segments, ts_start, ts_end)
        midpoint = (ts_start + ts_end) / 2.0
        time_ratio = midpoint / meeting_duration if meeting_duration > 0 else 0.0
        docs.append(
            Document(
                page_content=chunk["text"],
                metadata={
                    "meeting_id": meeting_id,
                    "chunk_index": i,
                    "timestamp_start": ts_start,
                    "timestamp_end": ts_end,
                    "speaker": chunk["speaker"],
                    "speakers_in_chunk": _SPEAKER_DELIMITER.join(chunk_speakers),
                    "multi_speaker": chunk.get("multi_speaker", False),
                    "time_position_ratio": round(time_ratio, 4),
                    "meeting_duration": round(meeting_duration, 2),
                    **metadata,
                },
            )
        )
    prefix = _chunk_id_prefix(
        meeting_id,
        metadata.get("file_id"),
        metadata.get("source_kind"),
        metadata.get("index_generation"),
    )
    logical_prefix = _chunk_id_prefix(
        meeting_id,
        metadata.get("file_id"),
        metadata.get("source_kind"),
    )
    for i, doc in enumerate(docs):
        doc.metadata["chunk_id"] = f"{prefix}_chunk_{i}"
        doc.metadata["logical_chunk_id"] = f"{logical_prefix}_chunk_{i}"
    ids = [f"{prefix}_chunk_{i}" for i in range(len(docs))]

    # 7. Dedup and persist (reusing embeddings)
    vectorstore = target_vs or get_vectorstore()
    new_docs, new_ids = _dedup_existing_chunks(docs, ids, vectorstore)
    if not new_docs:
        logger.info("Meeting %d: all %d segment chunks unchanged", meeting_id, len(docs))
        return

    # Map new_ids back to chunk embeddings (dedup may have removed some)
    id_to_embedding = {f"{prefix}_chunk_{i}": chunks[i]["embedding"] for i in range(len(chunks))}
    new_embeddings = [id_to_embedding[chunk_id] for chunk_id in new_ids]

    # Pass pre-computed embeddings to avoid recomputation
    _upsert_with_trace(vectorstore, new_docs, new_ids, trace, meeting_id, embeddings=new_embeddings)

    if not skip_bm25:
        _add_docs_to_bm25(meeting_id, docs, ids, strict=strict_bm25)


def _index_flat(
    meeting_id: int,
    text: str,
    metadata: dict,
    separators: list[str],
    trace: TraceContext | None = None,
    target_vs: Any = None,
) -> None:
    """Standard flat chunking with optional structure-aware splitting."""
    if trace:
        trace.start_span("chunk", "index")
    try:
        if settings.SEMANTIC_CHUNKING_ENABLED:
            chunks = _split_by_structure(text, max_chunk_size=settings.CHUNK_SIZE)
            splitter = _token_splitter()
            final_chunks: list[str] = []
            for chunk in chunks:
                token_size = getattr(settings, "CHUNK_SIZE_TOKENS", None)
                if not isinstance(token_size, int):
                    token_size = int(settings.CHUNK_SIZE)
                if count_tokens(chunk) > token_size:
                    final_chunks.extend(splitter.split_text(chunk))
                else:
                    final_chunks.append(chunk)
            chunks = final_chunks
        else:
            splitter = _token_splitter()
            chunks = splitter.split_text(text)
    finally:
        if trace:
            trace.finish_span("chunk")

    if not chunks:
        logger.warning("Meeting %d: empty text, skipping index", meeting_id)
        return

    prefix = _chunk_id_prefix(
        meeting_id,
        metadata.get("file_id"),
        metadata.get("source_kind"),
        metadata.get("index_generation"),
    )
    logical_prefix = _chunk_id_prefix(
        meeting_id,
        metadata.get("file_id"),
        metadata.get("source_kind"),
    )
    docs = [
        Document(
            page_content=chunk,
            metadata={
                "meeting_id": meeting_id,
                "chunk_index": i,
                "chunk_id": f"{prefix}_chunk_{i}",
                "logical_chunk_id": f"{logical_prefix}_chunk_{i}",
                "file_id": metadata.get("file_id"),
                "file_type": metadata.get("file_type"),
                **{k: v for k, v in metadata.items() if k not in ("file_id", "file_type")},
            },
        )
        for i, chunk in enumerate(chunks)
    ]
    ids = [f"{prefix}_chunk_{i}" for i in range(len(docs))]

    vectorstore = target_vs or get_vectorstore()
    new_docs, new_ids = _dedup_existing_chunks(docs, ids, vectorstore)
    if not new_docs:
        logger.info("Meeting %d: all %d chunks unchanged, skipping", meeting_id, len(docs))
        return

    _upsert_with_trace(vectorstore, new_docs, new_ids, trace, meeting_id)


def _index_parent_child(
    meeting_id: int,
    text: str,
    metadata: dict,
    separators: list[str],
    trace: TraceContext | None = None,
    target_vs: Any = None,
) -> None:
    """Two-level chunking: parent chunks for context, child chunks for retrieval."""
    parent_splitter = _token_splitter()
    child_splitter = _token_splitter(child=True)

    if trace:
        trace.start_span("chunk", "index")
    try:
        parent_chunks = parent_splitter.split_text(text)
        if not parent_chunks:
            if trace:
                trace.finish_span("chunk")
            logger.warning("Meeting %d: empty text, skipping index", meeting_id)
            return

        # Compute character offsets for each parent chunk in the source text.
        # This enables parent resolution by content when parent_id drifts
        # after chunk_size / overlap changes (C-RAG-1).
        parent_offsets: list[tuple[int, int]] = []
        search_start = 0
        for parent_text in parent_chunks:
            offset = text.find(parent_text, search_start)
            if offset >= 0:
                parent_offsets.append((offset, offset + len(parent_text)))
                search_start = offset + 1
            else:
                parent_offsets.append((-1, -1))

        all_docs: list[Document] = []
        all_ids: list[str] = []
        prefix = _chunk_id_prefix(
            meeting_id,
            metadata.get("file_id"),
            metadata.get("source_kind"),
            metadata.get("index_generation"),
        )
        logical_prefix = _chunk_id_prefix(
            meeting_id,
            metadata.get("file_id"),
            metadata.get("source_kind"),
        )

        def _offset_meta(start: int, end: int) -> dict:
            return {"parent_start_offset": start, "parent_end_offset": end} if start >= 0 else {}

        for i, parent_text in enumerate(parent_chunks):
            parent_id = f"{prefix}_parent_{i}"
            logical_parent_id = f"{logical_prefix}_parent_{i}"
            p_start, p_end = parent_offsets[i]
            all_docs.append(
                Document(
                    page_content=parent_text,
                    metadata={
                        **metadata,
                        "meeting_id": meeting_id,
                        "chunk_index": i,
                        "chunk_id": parent_id,
                        "logical_chunk_id": logical_parent_id,
                        "chunk_type": "parent",
                        **_offset_meta(p_start, p_end),
                    },
                )
            )
            all_ids.append(parent_id)

            child_chunks = child_splitter.split_text(parent_text)
            for j, child_text in enumerate(child_chunks):
                child_id = f"{prefix}_child_{i}_{j}"
                logical_child_id = f"{logical_prefix}_child_{i}_{j}"
                all_docs.append(
                    Document(
                        page_content=child_text,
                        metadata={
                            **metadata,
                            "meeting_id": meeting_id,
                            "chunk_index": i * 1000 + j,
                            "chunk_id": child_id,
                            "logical_chunk_id": logical_child_id,
                            "chunk_type": "child",
                            "parent_id": parent_id,
                            "logical_parent_id": logical_parent_id,
                            **_offset_meta(p_start, p_end),
                        },
                    )
                )
                all_ids.append(child_id)
    finally:
        if trace:
            trace.finish_span("chunk")

    vectorstore = target_vs or get_vectorstore()
    new_docs, new_ids = _dedup_existing_chunks(all_docs, all_ids, vectorstore)
    if not new_docs:
        logger.info("Meeting %d: all chunks unchanged, skipping", meeting_id)
        return

    _upsert_with_trace(vectorstore, new_docs, new_ids, trace, meeting_id)
    logger.info(
        "Meeting %d: indexed %d/%d chunks (parents + children)",
        meeting_id,
        len(new_docs),
        len(all_docs),
    )

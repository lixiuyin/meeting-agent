"""Legacy vector entries must retain real IDs through public citation formatting."""

from unittest.mock import Mock, patch

from src.services.chain._formatting import _extract_sources
from src.services.rag._retriever import _vector_sibling_fallback
from src.services.rag._vector import (
    _resolve_parents_by_offset,
    resolve_parent_chunks_by_ids,
    source_metadata,
)


def test_legacy_parent_record_id_reaches_public_citation():
    metadata = {"meeting_id": 1, "file_id": 2, "chunk_index": 3}
    store = Mock()
    store.get.return_value = {
        "ids": ["immutable-parent-generation-7"],
        "documents": ["Decision and supporting discussion."],
        "metadatas": [metadata],
    }
    with patch("src.services.rag._vectorstore.get_vectorstore", return_value=store):
        docs = resolve_parent_chunks_by_ids(
            ["immutable-parent-generation-7"], {"immutable-parent-generation-7": 0.2}
        )
    sources = _extract_sources(docs)
    assert sources[0]["chunk_id"] == "immutable-parent-generation-7"
    assert "chunk_id" not in metadata


def test_vector_record_identity_overrides_stale_metadata_without_fabrication():
    assert source_metadata({"chunk_id": "old"}, "actual")["chunk_id"] == "actual"
    assert source_metadata({"file_id": 2}) == {"file_id": 2}


def test_sql_sibling_preserves_record_identity():
    from src.core.database.bm25 import get_page_sibling_chunks

    conn = Mock()
    conn.execute.return_value.fetchall.return_value = [
        {
            "chunk_id": "sibling-sql",
            "content": "Table values",
            "metadata": '{"meeting_id":1,"file_id":2,"chunk_index":4}',
        }
    ]
    rows = get_page_sibling_chunks(
        conn, meeting_id=1, file_id=2, page_number=1, exclude_chunk_index=3
    )
    assert _extract_sources(rows)[0]["chunk_id"] == "sibling-sql"


def test_vector_sibling_preserves_record_identity():
    store = Mock()
    store.get.return_value = {
        "documents": ["Table values"],
        "ids": ["sibling-vector"],
        "metadatas": [{"meeting_id": 1, "file_id": 2, "chunk_index": 4}],
    }
    with patch("src.services.rag._retriever.get_vectorstore", return_value=store):
        rows = _vector_sibling_fallback(
            [{"metadata": {"meeting_id": 1, "file_id": 2, "page_number": 1}}],
            already_seen=set(),
            max_total=1,
        )
    assert _extract_sources(rows)[0]["chunk_id"] == "sibling-vector"


def test_vector_sibling_strips_contextual_index_prefix():
    prefix = "[Retrieval context: meeting=Jobs; file=slides.pdf]\n"
    store = Mock()
    store.get.return_value = {
        "documents": [prefix + "Table values"],
        "ids": ["sibling-vector-clean"],
        "metadatas": [
            {
                "meeting_id": 1,
                "file_id": 2,
                "chunk_index": 4,
                "retrieval_context_prefix_len": len(prefix),
            }
        ],
    }
    with patch("src.services.rag._retriever.get_vectorstore", return_value=store):
        rows = _vector_sibling_fallback(
            [{"metadata": {"meeting_id": 1, "file_id": 2, "page_number": 1}}],
            already_seen=set(),
            max_total=1,
        )
    assert rows[0]["content"] == "Table values"


def test_offset_parent_fallback_strips_contextual_index_prefix():
    prefix = "[Retrieval context: meeting=Jobs; file=slides.pdf]\n"
    store = Mock()
    store.get.return_value = {
        "documents": [prefix + "Parent content"],
        "ids": ["parent-vector-clean"],
        "metadatas": [
            {
                "meeting_id": 1,
                "file_id": 2,
                "parent_start_offset": 10,
                "parent_end_offset": 20,
                "retrieval_context_prefix_len": len(prefix),
            }
        ],
    }
    rows: list[dict] = []
    _resolve_parents_by_offset(store, {(10, 20): 0.2}, rows, lower_is_better=False)
    assert rows[0]["content"] == "Parent content"


def test_derived_summary_identity_is_separate_from_original_chunk_identity():
    metadata = {"meeting_id": 1, "file_id": 2, "source_kind": "file_summary"}
    first = _extract_sources([{"content": "First summary", "metadata": metadata}])[0]
    same = _extract_sources([{"content": "First summary", "metadata": metadata}])[0]
    revised = _extract_sources([{"content": "Revised summary", "metadata": metadata}])[0]
    assert first["source_id"] == same["source_id"]
    assert first["source_id"] != revised["source_id"]
    assert first["source_id"].startswith("file_summary:")
    assert first["chunk_id"] is None


def test_expanded_evidence_exposes_its_window_without_relabeling_the_anchor():
    source = _extract_sources(
        [
            {
                "content": "Expanded source",
                "metadata": {
                    "meeting_id": 1,
                    "file_id": 2,
                    "chunk_id": "original-anchor",
                    "evidence_start_offset": 100,
                    "evidence_end_offset": 400,
                },
            }
        ]
    )[0]
    assert source["chunk_id"] == "original-anchor"
    assert source["source_id"] == "original-anchor"
    assert (source["window_start"], source["window_end"]) == (100, 400)

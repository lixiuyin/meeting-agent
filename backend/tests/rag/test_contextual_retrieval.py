from src.services.rag._contextual import (
    contextualize_content,
    infer_material_role,
    restore_display_content,
)


def test_contextual_retrieval_is_lossless_for_display() -> None:
    original = "Alice confirmed the launch date is October 8."
    indexed, metadata = contextualize_content(
        original,
        {
            "meeting_title": "Atlas planning",
            "file_name": "roadmap.pdf",
            "heading_path": ["Q4", "Launch"],
            "speaker": "Alice",
            "timestamp_start": 61.5,
            "timestamp_end": 70.0,
        },
    )

    assert indexed.startswith("[Retrieval context:")
    assert "meeting=Atlas planning" in indexed
    assert "section=Q4 > Launch" in indexed
    assert restore_display_content(indexed, metadata) == original


def test_contextual_retrieval_leaves_context_free_chunks_unchanged() -> None:
    content = "plain chunk"
    indexed, metadata = contextualize_content(content, {"meeting_id": 1})

    assert indexed == content
    assert restore_display_content(indexed, metadata) == content


def test_context_hint_is_indexed_but_losslessly_removed_for_display() -> None:
    content = "He will own the follow-up."
    indexed, metadata = contextualize_content(
        content,
        {"meeting_id": 1, "context_hint": "Project Atlas release discussion with Alice."},
    )

    assert "context=Project Atlas release discussion with Alice." in indexed
    assert restore_display_content(indexed, metadata) == content


def test_meeting_material_roles_are_specialized_for_retrieval() -> None:
    assert infer_material_role("weekly-agenda.pdf", "pdf") == "agenda"
    assert infer_material_role("项目纪要.docx", "doc") == "minutes"
    assert infer_material_role("call.mp4", "video") == "attachment"
    assert infer_material_role("meeting-recording.mp4", "video") == "transcript"
    assert infer_material_role("architecture.pdf", "pdf") == "attachment"

    indexed, _metadata = contextualize_content(
        "Approve the launch.", {"material_role": "decision_log"}
    )
    assert "role=decision_log" in indexed

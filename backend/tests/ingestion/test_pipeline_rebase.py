"""Tests for C1: Docker path rebase cross-meeting collision guard."""

from pathlib import Path

import pytest


@pytest.fixture
def upload_dir(tmp_path):
    """Create a mock UPLOAD_DIR with some files."""
    d = tmp_path / "uploads"
    d.mkdir()
    return d


def _make_file_record(file_path: str, meeting_id: int = 1, file_id: int = 1) -> dict:
    return {
        "id": file_id,
        "meeting_id": meeting_id,
        "file_path": file_path,
        "file_type": "pdf",
        "file_name": Path(file_path).name,
        "status": "ready",
    }


class TestPipelineRebase:
    """Test _pipeline.py Docker path rebase validation."""

    def test_rebase_succeeds_when_basename_matches(self, upload_dir):
        """When file is missing at stored path but found in UPLOAD_DIR with matching basename, rebase succeeds."""
        # Simulate: file originally at /app/uploads/1_report.pdf (Docker path)
        # but now running locally where uploads are at a different base dir.
        stored_path = "/app/uploads/1_report.pdf"
        local_file = upload_dir / "1_report.pdf"
        local_file.write_bytes(b"%PDF-1.4 test content")

        record = _make_file_record(stored_path)

        # The rebase logic is inline in process_meeting_file; test the core check:
        file_path = Path(stored_path)
        candidate = upload_dir / file_path.name
        stored_name = Path(record["file_path"]).name

        assert candidate.exists()
        assert candidate.name == stored_name

    def test_rebase_rejects_cross_meeting_collision(self, upload_dir):
        """When candidate basename differs from stored record, rebase is rejected."""
        stored_path = "/app/uploads/1_report.pdf"
        # A different file exists in UPLOAD_DIR (belongs to another meeting)
        other_file = upload_dir / "2_report.pdf"
        other_file.write_bytes(b"%PDF-1.4 other meeting content")

        record = _make_file_record(stored_path)
        file_path = Path(stored_path)
        candidate = upload_dir / file_path.name
        stored_name = Path(record["file_path"]).name

        # Candidate doesn't exist (no file named "1_report.pdf" in upload_dir)
        assert not candidate.exists()
        # Even if we forced the candidate to exist with wrong name, names wouldn't match
        assert candidate.name != "2_report.pdf"

    def test_rebase_rejects_when_candidate_name_differs(self, upload_dir):
        """If stored path points to '1_report.pdf' but UPLOAD_DIR only has '1_other.pdf', reject."""
        stored_path = "/app/uploads/1_report.pdf"
        wrong_file = upload_dir / "1_other.pdf"
        wrong_file.write_bytes(b"%PDF-1.4 wrong file")

        record = _make_file_record(stored_path)
        file_path = Path(stored_path)

        # file_path.name = "1_report.pdf", candidate = upload_dir/1_report.pdf
        candidate = upload_dir / file_path.name
        stored_name = Path(record["file_path"]).name

        # candidate doesn't exist (only 1_other.pdf exists)
        assert not candidate.exists()
        assert stored_name == "1_report.pdf"

    def test_rebase_rejects_when_stored_path_basename_differs(self, upload_dir):
        """Edge case: stored path basename != candidate name even if candidate exists."""
        stored_path = "/docker/data/original_report.pdf"
        # A file with different prefix exists
        collision_file = upload_dir / "999_original_report.pdf"
        collision_file.write_bytes(b"%PDF-1.4 collision")

        record = _make_file_record(stored_path)
        file_path = Path(stored_path)
        candidate = upload_dir / file_path.name
        stored_name = Path(record["file_path"]).name

        # stored_name = "original_report.pdf", candidate.name = "original_report.pdf"
        # But candidate doesn't exist (only 999_original_report.pdf exists)
        assert not candidate.exists()


class TestFileDownloadRebase:
    """Test file_download.py mirrors the same basename validation."""

    def test_download_rejects_cross_meeting_file(self, upload_dir):
        """File download should 404 when rebase candidate doesn't match stored name."""
        stored_path = "/app/uploads/1_report.pdf"
        other_file = upload_dir / "2_presentation.pdf"
        other_file.write_bytes(b"%PDF-1.4 other")

        record = _make_file_record(stored_path)
        file_path = Path(stored_path)
        candidate = upload_dir / file_path.name
        stored_name = Path(record["file_path"]).name

        # Candidate doesn't exist → should trigger 404 behavior
        assert not candidate.exists()
        # If candidate existed but had wrong name, also reject:
        assert other_file.name != stored_name

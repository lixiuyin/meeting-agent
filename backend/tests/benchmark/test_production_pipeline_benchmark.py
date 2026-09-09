from __future__ import annotations

from pathlib import Path

import pytest

from scripts.production_pipeline_benchmark import _overlap, _resolve_upload_path, _tokens


def test_tokens_treat_markdown_as_formatting_and_preserve_cjk() -> None:
    assert _tokens("# Total: 42, 中文") == ["total", "42", "中", "文"]


def test_overlap_reports_multiset_recall_precision_and_f1() -> None:
    result = _overlap("alpha alpha beta", "alpha beta extra")

    assert result["matching_tokens"] == 2
    assert result["token_recall"] == pytest.approx(2 / 3)
    assert result["token_precision"] == pytest.approx(2 / 3)
    assert result["token_f1"] == pytest.approx(2 / 3)


def test_resolve_upload_path_accepts_stale_prefix_by_basename(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    expected = uploads / "document.pdf"
    expected.write_bytes(b"data")

    resolved = _resolve_upload_path("/old/deployment/uploads/document.pdf", uploads)

    assert resolved == expected.resolve()


def test_resolve_upload_path_rejects_existing_file_outside_root(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    assert _resolve_upload_path(str(outside), uploads) is None

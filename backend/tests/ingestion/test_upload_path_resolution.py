"""Stored upload paths may be rebased only with byte-level identity proof."""

import hashlib

import pytest

from src.core.config import settings
from src.services.files._paths import resolve_upload_path


def test_missing_absolute_path_rebases_when_hash_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    candidate = tmp_path / "recording.wav"
    candidate.write_bytes(b"same bytes")
    expected = hashlib.sha256(b"same bytes").hexdigest()

    resolved = resolve_upload_path("/old/container/recording.wav", expected_hash=expected)

    assert resolved == candidate.resolve()


def test_missing_absolute_path_rejects_wrong_basename_match(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    candidate = tmp_path / "recording.wav"
    candidate.write_bytes(b"different bytes")
    expected = hashlib.sha256(b"original bytes").hexdigest()

    with pytest.raises(ValueError, match="content hash"):
        resolve_upload_path("/old/container/recording.wav", expected_hash=expected)

import pytest

from src.core import chroma_security


def test_chroma_runtime_rejects_remote_and_trust_remote_code(monkeypatch, tmp_path):
    monkeypatch.setattr(chroma_security.settings, "CHROMA_REMOTE_ENABLED", True)
    with pytest.raises(RuntimeError, match="Remote Chroma"):
        chroma_security.validate_chroma_runtime(persist_dir=tmp_path)

    monkeypatch.setattr(chroma_security.settings, "CHROMA_REMOTE_ENABLED", False)
    monkeypatch.setattr(chroma_security.settings, "CHROMA_TRUST_REMOTE_CODE", True)
    with pytest.raises(RuntimeError, match="trust_remote_code"):
        chroma_security.validate_chroma_runtime(persist_dir=tmp_path)


def test_chroma_runtime_requires_data_root_in_production(monkeypatch, tmp_path):
    monkeypatch.setattr(chroma_security.settings, "CHROMA_TRUST_REMOTE_CODE", False)
    monkeypatch.setattr(chroma_security.settings, "CHROMA_REMOTE_ENABLED", False)
    monkeypatch.setattr(chroma_security.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(chroma_security, "DATA_DIR", tmp_path / "data")

    with pytest.raises(RuntimeError, match="inside DATA_DIR"):
        chroma_security.validate_chroma_runtime(persist_dir=tmp_path / "outside")

    assert chroma_security.validate_chroma_runtime(
        persist_dir=tmp_path / "data" / "vectordb"
    ).is_dir()

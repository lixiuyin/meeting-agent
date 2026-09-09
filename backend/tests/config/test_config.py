"""Tests for configuration"""

import pytest


class TestSettings:
    def test_settings_from_env(self, monkeypatch):
        """Test that settings can be loaded from environment variables"""
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("LLM_TEMPERATURE", "0.5")
        monkeypatch.setenv("API_KEY", "test-api-key")

        # Import after setting env vars - create new instance to pick up env
        from src.core.config import Settings

        settings_new = Settings()
        assert settings_new.LLM_MODEL == "gpt-4o"
        assert settings_new.LLM_TEMPERATURE == 0.5
        assert settings_new.API_KEY.get_secret_value() == "test-api-key"

    def test_blank_optional_principal_id_is_unset(self, monkeypatch):
        from src.core.config import Settings

        monkeypatch.setenv("PRINCIPAL_ID", "")

        assert Settings().PRINCIPAL_ID is None

    def test_settings_basic_values(self):
        """Test that settings can be accessed and have expected structure"""
        from src.core.config import settings

        # These are the values from conftest.py (not defaults, but expected test values)
        # This verifies the settings system is working correctly
        assert settings.LLM_BINDING is not None
        assert settings.LLM_MODEL is not None
        assert settings.TOP_K > 0
        assert settings.CHUNK_SIZE > 0

    def test_max_upload_bytes_calculation(self):
        """Test MAX_UPLOAD_BYTES property"""
        from src.core.config import Settings
        from src.core.config import settings as _settings

        _ = Settings()
        _settings.MAX_UPLOAD_SIZE_MB = 100
        assert _settings.MAX_UPLOAD_BYTES == 100 * 1024 * 1024

    def test_settings_directories_created(self, tmp_path, monkeypatch):
        """Test that data directories are created on init"""
        data_dir = tmp_path / "test_data"
        upload_dir = data_dir / "uploads"
        vectordb_dir = data_dir / "vectordb"

        monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
        monkeypatch.setenv("VECTOR_DB_DIR", str(vectordb_dir))

        from src.core.config import Settings

        _ = Settings()

        # Directories should exist
        assert upload_dir.exists()
        assert vectordb_dir.exists()

    @pytest.mark.parametrize(
        ("configured", "canonical"),
        [("DEV", "dev"), ("staging", "staging"), ("prod", "production")],
    )
    def test_environment_is_normalized(self, configured, canonical, monkeypatch):
        from src.core.config import Settings

        monkeypatch.setenv("ENVIRONMENT", configured)
        if canonical != "dev":
            monkeypatch.setenv("API_KEY", "test-api-key")
            monkeypatch.setenv("PRINCIPAL_PEPPER", "test-principal-pepper")
            monkeypatch.setenv("CORS_ORIGINS", "https://example.test")
            monkeypatch.setenv("TRUSTED_HOSTS", "example.test")

        assert canonical == Settings().ENVIRONMENT

    def test_invalid_environment_fails_closed(self, monkeypatch):
        from src.core.config import Settings

        monkeypatch.setenv("ENVIRONMENT", "prd")
        with pytest.raises(ValueError, match="Invalid ENVIRONMENT"):
            Settings()

    def test_non_dev_rejects_current_raganything_dependency_chain(self, monkeypatch):
        from src.core.config import Settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("RAGANYTHING_ENABLED", "true")
        with pytest.raises(ValueError, match="temporarily blocked"):
            Settings()

    def test_hybrid_provider_requires_enabled_gate(self, monkeypatch):
        from src.core.config import Settings

        monkeypatch.setenv("RAG_RETRIEVER_PROVIDER", "hybrid")
        monkeypatch.setenv("HYBRID_SEARCH_ENABLED", "false")
        with pytest.raises(ValueError, match="requires HYBRID_SEARCH_ENABLED=true"):
            Settings()

    def test_legacy_native_provider_is_normalized(self, monkeypatch):
        from src.core.config import Settings

        monkeypatch.setenv("RAG_RETRIEVER_PROVIDER", "native")
        monkeypatch.setenv("HYBRID_SEARCH_ENABLED", "false")
        assert Settings().RAG_RETRIEVER_PROVIDER == "vector"

    def test_memory_scoring_weights_are_normalized_at_runtime(self, monkeypatch):
        from src.core.config import Settings

        monkeypatch.setenv("MEMORY_SCORING_SEMANTIC_WEIGHT", "0.8")
        monkeypatch.setenv("MEMORY_SCORING_DECAY_WEIGHT", "0.4")
        monkeypatch.setenv("MEMORY_SCORING_IMPORTANCE_WEIGHT", "0.3")
        configured = Settings()
        assert configured.MEMORY_SCORING_SEMANTIC_WEIGHT == 0.8
        assert configured.MEMORY_SCORING_DECAY_WEIGHT == 0.4
        assert configured.MEMORY_SCORING_IMPORTANCE_WEIGHT == 0.3

    def test_memory_scoring_weights_cannot_all_be_zero(self, monkeypatch):
        from src.core.config import Settings

        for name in (
            "MEMORY_SCORING_SEMANTIC_WEIGHT",
            "MEMORY_SCORING_DECAY_WEIGHT",
            "MEMORY_SCORING_IMPORTANCE_WEIGHT",
            "MEMORY_SCORING_CONFIDENCE_WEIGHT",
            "MEMORY_SCORING_USEFULNESS_WEIGHT",
        ):
            monkeypatch.setenv(name, "0")
        with pytest.raises(ValueError, match=r"(?i)at least one"):
            Settings()


class TestConstants:
    def test_paths_are_absolute(self):
        """Test that all paths in constants are absolute"""
        from src.core.constants import (
            CONFIG_DIR,
            DATA_DIR,
            DB_PATH,
            UPLOAD_DIR,
            VECTOR_DB_DIR,
        )

        assert DATA_DIR.is_absolute()
        assert DB_PATH.is_absolute()
        assert UPLOAD_DIR.is_absolute()
        assert VECTOR_DB_DIR.is_absolute()
        assert CONFIG_DIR.is_absolute()

    def test_paths_are_relative_to_project(self):
        """Test that paths are derived from project root (skipped when patched for tests)"""
        from src.core.constants import CONFIG_DIR, DATA_DIR, PROJECT_ROOT

        # When running tests, conftest.py patches DATA_DIR to a temp directory
        # for test isolation. Skip this assertion in that case.
        if "tmp" in str(DATA_DIR) or "var/folders" in str(DATA_DIR):
            pytest.skip("DATA_DIR is patched to temp directory for tests")

        # DATA_DIR should be inside PROJECT_ROOT
        assert DATA_DIR.is_relative_to(PROJECT_ROOT)
        assert CONFIG_DIR.is_relative_to(PROJECT_ROOT)

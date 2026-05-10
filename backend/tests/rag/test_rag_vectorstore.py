"""Tests for RAG vector store operations."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services import rag  # noqa: E402
from src.services.rag import _vectorstore as _vs_module  # noqa: E402


class TestVectorStoreOperations:
    """Test vector store singleton and operations."""

    @pytest.fixture(autouse=True)
    def reset_vectorstore(self):
        """Reset vectorstore singleton before each test."""
        _vs_module._vectorstore = None
        yield
        _vs_module._vectorstore = None

    def test_get_vectorstore_returns_singleton(self):
        """get_vectorstore should return the same instance."""
        with (
            patch("src.services.rag._vectorstore.Chroma") as mock_chroma,
            patch("src.services.rag._vectorstore.get_embeddings") as mock_embed,
        ):
            mock_instance = MagicMock()
            mock_chroma.return_value = mock_instance
            mock_embed.return_value = MagicMock()

            # First call creates instance
            vs1 = rag.get_vectorstore()
            # Second call returns same instance
            vs2 = rag.get_vectorstore()

            assert vs1 is vs2

    def test_get_vectorstore_with_embeddings(self):
        """Should create vectorstore with embeddings function."""
        with patch("src.services.rag._vectorstore.Chroma") as mock_chroma:
            mock_instance = MagicMock()
            mock_chroma.return_value = mock_instance

            with patch("src.services.rag._vectorstore.get_embeddings") as mock_embed:
                mock_embed.return_value = MagicMock()
                _ = rag.get_vectorstore()

                mock_chroma.assert_called_once()
                call_kwargs = mock_chroma.call_args.kwargs
                assert "embedding_function" in call_kwargs

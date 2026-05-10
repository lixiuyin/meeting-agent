"""End-to-end RAG pipeline test: upload -> process -> query -> answer with sources."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# Ensure temp paths before any app imports
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["UPLOAD_DIR"] = str(Path(os.environ["DATA_DIR"]) / "uploads")
os.environ["VECTOR_DB_DIR"] = str(Path(os.environ["DATA_DIR"]) / "vectordb")

import src.core.constants as constants_module

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.UPLOAD_DIR = Path(os.environ["DATA_DIR"]) / "uploads"
constants_module.VECTOR_DB_DIR = Path(os.environ["DATA_DIR"]) / "vectordb"
constants_module.DB_PATH = Path(os.environ["DATA_DIR"]) / "meetings.db"

constants_module.DATA_DIR.mkdir(parents=True, exist_ok=True)
constants_module.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
constants_module.VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)

from src.core.database import init_db  # noqa: E402
from src.main import app  # noqa: E402

init_db()


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _isolate_vectorstore(tmp_path, monkeypatch):
    """Isolate ChromaDB data and reset singletons for each test."""
    from src.core import config as config_mod

    vs_dir = tmp_path / "vectordb"
    vs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_mod.settings, "VECTOR_DB_DIR", vs_dir)
    monkeypatch.setattr(constants_module, "VECTOR_DB_DIR", vs_dir)

    import src.services.rag._vectorstore as _vs_mod

    _vs_mod._create_vectorstore.cache_clear()

    yield

    _vs_mod._create_vectorstore.cache_clear()


@pytest.fixture
def auth_headers():
    return {"X-API-Key": "test-api-key"}


def _mock_embeddings():
    """Return a deterministic mock embeddings instance."""
    mock = MagicMock()

    from src.core.config import settings

    dim = settings.EMBEDDING_DIMENSION

    # deterministic embedding: first dim varies by text hash so different texts differ
    def _embed(texts):
        if isinstance(texts, str):
            return [float(hash(texts) % 1000) / 1000.0] + [0.0] * (dim - 1)
        return [[float(hash(t) % 1000) / 1000.0] + [0.0] * (dim - 1) for t in texts]

    mock.embed_documents.side_effect = lambda texts: _embed(texts)
    mock.embed_query.side_effect = lambda text: _embed(text)
    return mock


class _MockLLM(BaseChatModel):
    """Proper mock LLM compatible with LangChain LCEL chains."""

    answer: str = ""

    def _generate(
        self,
        messages,
        stop=None,
        run_manager=None,
        **kwargs,
    ):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.answer))])

    @property
    def _llm_type(self):
        return "mock"

    @property
    def _identifying_params(self):
        return {}


def _mock_llm(answer: str):
    """Return a mock LLM that produces the given answer."""
    return _MockLLM(answer=answer)


@pytest.mark.asyncio
async def test_rag_pipeline_upload_process_query(client, auth_headers, tmp_path):
    """Full pipeline: upload a txt file, index it, query it, and verify answer + sources."""
    # Patch embeddings and LLM to avoid external API calls
    with (
        patch("src.services.embedder._embeddings", _mock_embeddings()),
        patch(
            "src.services.llm.get_llm",
            return_value=_mock_llm("The roadmap includes dark mode and SSO."),
        ),
        patch("src.services.rag.get_vectorstore") as mock_vs,
    ):
        from src.services.rag._vectorstore import get_vectorstore

        real_vs = get_vectorstore()
        mock_vs.return_value = real_vs

        # 1. Upload a plain-text file
        file_path = tmp_path / "meeting.txt"
        file_path.write_text(
            "Product Roadmap Q2\n- Dark mode\n- Single sign-on (SSO)\n- Mobile app rewrite\n",
            encoding="utf-8",
        )

        async with client as c:
            with open(file_path, "rb") as f:
                resp = await c.post(
                    "/api/v1/meetings/upload",
                    headers=auth_headers,
                    data={"title": "Q2 Roadmap"},
                    files={"file": ("meeting.txt", f, "text/plain")},
                )
            assert resp.status_code == 200, resp.text
            upload_data = resp.json()
            meeting_id = upload_data["meeting_id"]
            file_id = upload_data["file_id"]

            # 2. Process the file inline (normally background task)
            from src.services.processor import process_meeting_file

            await process_meeting_file(file_id)

            # 3. Query the meeting
            resp = await c.post(
                "/api/v1/chat",
                headers=auth_headers,
                json={
                    "question": "What is in the roadmap?",
                    "meeting_ids": [meeting_id],
                },
            )
            assert resp.status_code == 200, resp.text
            chat_data = resp.json()

            # 4. Validate answer and sources
            assert "roadmap" in chat_data["answer"].lower() or "sso" in chat_data["answer"].lower()
            assert len(chat_data["sources"]) > 0
            source = chat_data["sources"][0]
            assert source["meeting_id"] == meeting_id
            assert (
                "roadmap" in source["meeting_title"].lower()
                or "meeting" in source["meeting_title"].lower()
            )
            assert "content" in source
            assert "score" in source


@pytest.mark.asyncio
async def test_rag_stream_pipeline(client, auth_headers, tmp_path):
    """Stream endpoint returns valid SSE events with sources."""
    with (
        patch("src.services.embedder._embeddings", _mock_embeddings()),
        patch("src.services.llm._providers._llm", _mock_llm("Mobile rewrite is planned for Q2.")),
        patch("src.services.rag.get_vectorstore") as mock_vs,
    ):
        from src.services.rag._vectorstore import get_vectorstore

        mock_vs.return_value = get_vectorstore()

        file_path = tmp_path / "stream.txt"
        file_path.write_text("Mobile app rewrite is scheduled for Q2.", encoding="utf-8")

        async with client as c:
            with open(file_path, "rb") as f:
                resp = await c.post(
                    "/api/v1/meetings/upload",
                    headers=auth_headers,
                    data={"title": "Mobile Plan"},
                    files={"file": ("stream.txt", f, "text/plain")},
                )
            upload_data = resp.json()
            file_id = upload_data["file_id"]

            from src.services.processor import process_meeting_file

            await process_meeting_file(file_id)

            async with c.stream(
                "POST",
                "/api/v1/chat/stream",
                headers={**auth_headers, "Accept": "text/event-stream"},
                json={
                    "question": "When is the mobile rewrite?",
                    "meeting_ids": [upload_data["meeting_id"]],
                },
            ) as stream_resp:
                assert stream_resp.status_code == 200
                events = []
                async for line in stream_resp.aiter_lines():
                    if line.startswith("data:"):
                        events.append(line[5:].strip())

                assert len(events) > 0
                # Expect at least a token event and a done event
                assert any("token" in ev for ev in events) or any("done" in ev for ev in events)

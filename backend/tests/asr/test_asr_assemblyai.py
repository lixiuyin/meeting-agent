"""Tests for the async AssemblyAI ASR client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.asr._assemblyai import (
    _clean_utterance_text,
    _format_plain,
    _format_segments,
    transcribe_assemblyai,
    transcribe_assemblyai_with_segments,
)

# ---------------------------------------------------------------------------
# _format_plain tests
# ---------------------------------------------------------------------------


class TestFormatPlain:
    def test_with_utterances(self):
        result = {
            "utterances": [
                {"speaker": "A", "text": "hello", "start": 5000},
                {"speaker": "B", "text": "world", "start": 96000},
            ]
        }
        assert _format_plain(result) == "[00:00:05] A: hello\n[00:01:36] B: world"

    def test_with_utterances_zero_start(self):
        result = {
            "utterances": [
                {"speaker": "A", "text": "hello"},
                {"speaker": "B", "text": "world"},
            ]
        }
        assert _format_plain(result) == "[00:00:00] A: hello\n[00:00:00] B: world"

    def test_no_utterances_falls_back_to_text(self):
        result = {"text": "just plain text"}
        assert _format_plain(result) == "just plain text"

    def test_empty_utterances_falls_back(self):
        result = {"utterances": [], "text": "fallback"}
        assert _format_plain(result) == "fallback"

    def test_strips_speaker_artifact(self):
        result = {
            "utterances": [
                {"speaker": "A", "text": "[Speaker] hello there", "start": 0},
            ]
        }
        assert _format_plain(result) == "[00:00:00] A: hello there"


class TestCleanUtteranceText:
    def test_strips_leading_marker(self):
        assert _clean_utterance_text("[Speaker] hello world") == "hello world"

    def test_strips_inline_marker(self):
        assert _clean_utterance_text("hello [Speaker] world") == "hello world"

    def test_collapses_double_whitespace(self):
        assert _clean_utterance_text("a  [Speaker]  b") == "a b"

    def test_empty_input(self):
        assert _clean_utterance_text("") == ""

    def test_no_marker_unchanged(self):
        assert _clean_utterance_text("plain text") == "plain text"


# ---------------------------------------------------------------------------
# _format_segments tests
# ---------------------------------------------------------------------------


class TestFormatSegments:
    def test_with_utterances(self):
        result = {
            "utterances": [
                {"speaker": "A", "text": "hello", "start": 1000, "end": 3000},
                {"speaker": "B", "text": "world", "start": 3500, "end": 6000},
            ]
        }
        segments = _format_segments(result)
        assert len(segments) == 2
        assert segments[0] == {
            "start": 1.0,
            "end": 3.0,
            "text": "hello",
            "speaker": "A",
        }
        assert segments[1] == {
            "start": 3.5,
            "end": 6.0,
            "text": "world",
            "speaker": "B",
        }

    def test_with_words_fallback(self):
        result = {
            "words": [
                {"text": "hello", "start": 0, "end": 1000, "speaker": "A"},
                {"text": "world", "start": 1100, "end": 2000, "speaker": "A"},
                {"text": "foo", "start": 2500, "end": 3000, "speaker": "B"},
            ]
        }
        segments = _format_segments(result)
        assert len(segments) == 2
        assert segments[0]["text"] == "hello world"
        assert segments[0]["speaker"] == "A"
        assert segments[1]["text"] == "foo"
        assert segments[1]["speaker"] == "B"

    def test_text_only_fallback(self):
        result = {"text": "full transcript"}
        segments = _format_segments(result)
        assert segments == [{"start": 0.0, "end": 0.0, "text": "full transcript"}]

    def test_empty_result(self):
        assert _format_segments({}) == []

    def test_strips_speaker_artifact_from_utterances(self):
        result = {
            "utterances": [
                {
                    "speaker": "A",
                    "text": "[Speaker] We found that...",
                    "start": 1000,
                    "end": 3000,
                },
            ]
        }
        segments = _format_segments(result)
        assert segments[0]["text"] == "We found that..."

    def test_strips_speaker_artifact_from_words_fallback(self):
        result = {
            "words": [
                {"text": "[Speaker]", "start": 0, "end": 100, "speaker": "A"},
                {"text": "hello", "start": 200, "end": 500, "speaker": "A"},
            ]
        }
        segments = _format_segments(result)
        assert "[Speaker]" not in segments[0]["text"]


# ---------------------------------------------------------------------------
# Integration tests with mocked HTTP
# ---------------------------------------------------------------------------


def _mock_response(data: dict) -> MagicMock:
    """Create a mock HTTP response."""
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


def _mock_settings(**overrides):
    """Create a mock settings object with AssemblyAI config."""
    mock = MagicMock()
    mock.ASSEMBLYAI_API_KEY = MagicMock()
    mock.ASSEMBLYAI_API_KEY.get_secret_value.return_value = "test-key-123"
    mock.ASSEMBLYAI_SPEECH_MODEL = "universal-3-pro"
    mock.ASSEMBLYAI_SPEAKER_LABELS = True
    mock.ASSEMBLYAI_LANGUAGE_DETECTION = True
    mock.ASSEMBLYAI_POLL_INTERVAL_SECONDS = 0  # no sleep in tests
    mock.ASSEMBLYAI_MAX_WAIT_SECONDS = 10
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


@pytest.fixture
def mock_settings():
    with patch("src.services.asr._assemblyai.settings", _mock_settings()):
        yield


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "test.mp3"
    p.write_bytes(b"fake audio data")
    return p


class TestTranscribeAssemblyAI:
    @pytest.mark.asyncio
    async def test_transcribe_plain(self, mock_settings, audio_file):
        mock_client = AsyncMock()
        # post: upload → submit (two calls)
        mock_client.post.side_effect = [
            _mock_response({"upload_url": "https://cdn.example.com/audio.mp3"}),
            _mock_response({"id": "tr-001"}),
        ]
        # get: poll — queued then completed
        mock_client.get.side_effect = [
            _mock_response({"status": "queued"}),
            _mock_response(
                {
                    "status": "completed",
                    "utterances": [
                        {"speaker": "A", "text": "hello"},
                        {"speaker": "B", "text": "world"},
                    ],
                    "text": "hello world",
                }
            ),
        ]

        with patch(
            "src.services.asr._assemblyai._get_http_client",
            return_value=mock_client,
        ):
            result = await transcribe_assemblyai(audio_file)

        assert result == "[00:00:00] A: hello\n[00:00:00] B: world"

    @pytest.mark.asyncio
    async def test_transcribe_no_utterances_falls_back_to_text(self, mock_settings, audio_file):
        mock_client = AsyncMock()
        mock_client.post.side_effect = [
            _mock_response({"upload_url": "https://cdn.example.com/audio.mp3"}),
            _mock_response({"id": "tr-002"}),
        ]
        mock_client.get.return_value = _mock_response(
            {"status": "completed", "text": "plain text only"}
        )

        with patch(
            "src.services.asr._assemblyai._get_http_client",
            return_value=mock_client,
        ):
            result = await transcribe_assemblyai(audio_file)

        assert result == "plain text only"

    @pytest.mark.asyncio
    async def test_transcribe_error_status(self, mock_settings, audio_file):
        mock_client = AsyncMock()
        mock_client.post.side_effect = [
            _mock_response({"upload_url": "https://cdn.example.com/audio.mp3"}),
            _mock_response({"id": "tr-003"}),
        ]
        mock_client.get.return_value = _mock_response(
            {"status": "error", "error": "bad audio format"}
        )

        with (
            patch(
                "src.services.asr._assemblyai._get_http_client",
                return_value=mock_client,
            ),
            pytest.raises(RuntimeError, match="bad audio format"),
        ):
            await transcribe_assemblyai(audio_file)

    @pytest.mark.asyncio
    async def test_transcribe_timeout(self, audio_file):
        mock_s = _mock_settings(
            ASSEMBLYAI_MAX_WAIT_SECONDS=0,
            ASSEMBLYAI_POLL_INTERVAL_SECONDS=1,
        )

        with patch("src.services.asr._assemblyai.settings", mock_s):
            mock_client = AsyncMock()
            mock_client.post.side_effect = [
                _mock_response({"upload_url": "https://cdn.example.com/audio.mp3"}),
                _mock_response({"id": "tr-004"}),
            ]
            # Always return "processing" so it never completes
            mock_client.get.return_value = _mock_response({"status": "processing"})

            with (
                patch(
                    "src.services.asr._assemblyai._get_http_client",
                    return_value=mock_client,
                ),
                patch("src.services.asr._assemblyai.asyncio.sleep", new_callable=AsyncMock),
            ):
                with pytest.raises(TimeoutError, match="timed out"):
                    await transcribe_assemblyai(audio_file)

    @pytest.mark.asyncio
    async def test_transcribe_missing_key(self, audio_file):
        mock_s = MagicMock()
        mock_s.ASSEMBLYAI_API_KEY = MagicMock()
        mock_s.ASSEMBLYAI_API_KEY.get_secret_value.return_value = ""

        with patch("src.services.asr._assemblyai.settings", mock_s):
            with pytest.raises(RuntimeError, match="ASSEMBLYAI_API_KEY is required"):
                await transcribe_assemblyai(audio_file)


class TestTranscribeWithSegments:
    @pytest.mark.asyncio
    async def test_with_segments(self, mock_settings, audio_file):
        mock_client = AsyncMock()
        mock_client.post.side_effect = [
            _mock_response({"upload_url": "https://cdn.example.com/audio.mp3"}),
            _mock_response({"id": "tr-005"}),
        ]
        mock_client.get.return_value = _mock_response(
            {
                "status": "completed",
                "utterances": [
                    {"speaker": "A", "text": "hello", "start": 1000, "end": 3000},
                    {"speaker": "B", "text": "world", "start": 3500, "end": 6000},
                ],
            }
        )

        with patch(
            "src.services.asr._assemblyai._get_http_client",
            return_value=mock_client,
        ):
            segments = await transcribe_assemblyai_with_segments(audio_file)

        assert len(segments) == 2
        assert segments[0]["start"] == 1.0
        assert segments[0]["end"] == 3.0
        assert segments[0]["speaker"] == "A"
        assert segments[1]["start"] == 3.5

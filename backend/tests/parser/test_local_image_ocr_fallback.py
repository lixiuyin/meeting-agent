"""Test local parser image OCR fallback via vision service."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import constants as constants_module

os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"
constants_module.CHROMA_PATH = constants_module.DATA_DIR / "chroma"
constants_module.UPLOAD_DIR = constants_module.DATA_DIR / "uploads"

from src.services.parser.providers.local import LocalFastParser  # noqa: E402


@pytest.fixture
def sample_image(tmp_path):
    """Create a minimal PNG file (1x1 pixel)."""
    import struct
    import zlib

    path = tmp_path / "test.png"
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)

    raw = zlib.compress(b"\x00\x00\x00\x00\x00")
    idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
    idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)

    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

    path.write_bytes(signature + ihdr + idat + iend)
    return path


def test_vision_fallback_when_no_local_ocr(sample_image):
    """When pytesseract and easyocr are unavailable, vision fallback provides text."""
    parser = LocalFastParser()

    with (
        patch.object(parser, "_try_tesseract", return_value=""),
        patch.object(parser, "_try_easyocr", return_value=""),
        patch(
            "src.services.vision.caption_image",
            return_value="A diagram showing workflow",
        ),
        patch(
            "src.services.vision.transcribe_text_bearing_image",
            return_value="Start -> Process -> End",
        ),
    ):
        result = parser.parse(sample_image)
        assert result.total_pages == 1
        assert result.pages[0].text
        assert "diagram" in result.pages[0].text
        assert "Start" in result.pages[0].text
        assert result.pages[0].metadata["ocr_engine"] == "vision"

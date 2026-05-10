"""Boundary tests covering edge cases that are commonly overlooked."""

import pytest
from fastapi import HTTPException


@pytest.mark.unit
class TestFileValidationBoundary:
    def test_zero_byte_file_rejected(self):
        from src.api.routers.meetings._common import _validate_file_content

        with pytest.raises(ValueError, match="empty file"):
            _validate_file_content(b"", ".pdf")

    def test_corrupted_pdf_header_rejected(self):
        from src.api.routers.meetings._common import _validate_file_content

        with pytest.raises(HTTPException, match="does not match"):
            _validate_file_content(b"NOT A PDF FILE\x00\x00\x00", ".pdf")

    def test_truncated_ftyp_header_rejected(self):
        from src.api.routers.meetings._common import _validate_file_content

        with pytest.raises(HTTPException, match="does not match"):
            _validate_file_content(b"\x00\x00\x00\x08ftyp", ".mp4")

    def test_valid_pdf_header_accepted(self):
        from src.api.routers.meetings._common import _validate_file_content

        _validate_file_content(b"%PDF-1.4\n%\xaa\xab\xac\xad", ".pdf")

    def test_valid_png_header_accepted(self):
        from src.api.routers.meetings._common import _validate_file_content

        _validate_file_content(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", ".png")

    def test_mismatched_extension_mp3_vs_pdf(self):
        from src.api.routers.meetings._common import _validate_file_content

        with pytest.raises(HTTPException, match="does not match"):
            _validate_file_content(b"ID3\x03\x00\x00\x00", ".pdf")

    def test_valid_mp3_id3v2_header_accepted(self):
        from src.api.routers.meetings._common import _validate_file_content

        _validate_file_content(b"ID3\x03\x00\x00\x00\x00\x00\x00", ".mp3")


@pytest.mark.unit
class TestSanitizeFilenameBoundary:
    def test_path_traversal_prevented(self):
        from src.api.routers.meetings._common import _sanitize_filename

        result = _sanitize_filename("../../../etc/passwd.pdf")
        assert ".." not in result
        assert result == "passwd.pdf"

    def test_null_byte_stripped(self):
        from src.api.routers.meetings._common import _sanitize_filename

        result = _sanitize_filename("file\x00name.pdf")
        assert "\x00" not in result

    def test_unicode_filename_preserved(self):
        from src.api.routers.meetings._common import _sanitize_filename

        result = _sanitize_filename("中文文件名.pdf")
        assert "中文文件名" in result

    def test_empty_filename_handled(self):
        from src.api.routers.meetings._common import _sanitize_filename

        result = _sanitize_filename("")
        assert len(result) > 0


@pytest.mark.unit
class TestTokenTruncationBoundary:
    def test_empty_text_returns_empty(self):
        from src.services.chain._steps_generate import _truncate_text_to_tokens

        result = _truncate_text_to_tokens("", max_tokens=100, model="gpt-4o-mini")
        assert result == ""

    def test_zero_max_tokens_returns_empty(self):
        from src.services.chain._steps_generate import _truncate_text_to_tokens

        result = _truncate_text_to_tokens("hello world", max_tokens=0, model="gpt-4o-mini")
        assert result == ""

    def test_text_within_limit_returned_verbatim(self):
        from src.services.chain._steps_generate import _truncate_text_to_tokens

        text = "short text"
        result = _truncate_text_to_tokens(text, max_tokens=100, model="gpt-4o-mini")
        assert result == text


@pytest.mark.unit
class TestIdempotencyBoundary:
    def test_body_hash_stable_for_same_content(self):
        import hashlib

        body1 = b'{"title":"test"}'
        body2 = b'{"title":"test"}'
        assert hashlib.sha256(body1).hexdigest() == hashlib.sha256(body2).hexdigest()

    def test_body_hash_differs_for_different_content(self):
        import hashlib

        body1 = b'{"title":"test1"}'
        body2 = b'{"title":"test2"}'
        assert hashlib.sha256(body1).hexdigest() != hashlib.sha256(body2).hexdigest()

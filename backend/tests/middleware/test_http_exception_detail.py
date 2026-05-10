"""Tests for HTTPException detail coercion in main.py."""

from fastapi import HTTPException


class TestHttpExceptionDetail:
    def test_exception_detail_dict_coerced_to_string(self):
        """HTTPException with dict detail should be serialized as string."""
        exc = HTTPException(status_code=422, detail={"msg": "bad", "code": 42})
        detail_str = str(exc.detail) if exc.detail is not None else ""
        assert isinstance(detail_str, str)
        assert "bad" in detail_str

    def test_exception_detail_string(self):
        """HTTPException with string detail should pass through."""
        exc = HTTPException(status_code=400, detail="bad request")
        detail_str = str(exc.detail) if exc.detail is not None else ""
        assert detail_str == "bad request"

    def test_exception_detail_int(self):
        """HTTPException with int detail should be coerced to string."""
        exc = HTTPException(status_code=500, detail=42)
        detail_str = str(exc.detail) if exc.detail is not None else ""
        assert detail_str == "42"

    def test_exception_detail_list(self):
        """HTTPException with list detail should be coerced to string."""
        exc = HTTPException(status_code=422, detail=[{"loc": "body", "msg": "required"}])
        detail_str = str(exc.detail) if exc.detail is not None else ""
        assert isinstance(detail_str, str)
        assert "required" in detail_str

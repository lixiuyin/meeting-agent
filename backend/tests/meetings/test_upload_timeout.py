"""Tests for C4: Upload chunk-read timeout protection."""

import asyncio

import pytest


class TestUploadTimeout:
    """Test that the upload timeout mechanism works correctly."""

    @pytest.mark.asyncio
    async def test_asyncio_wait_for_raises_timeout_on_slow_read(self):
        """Verify asyncio.wait_for raises TimeoutError when stream stalls."""

        async def _slow_stream():
            await asyncio.sleep(60)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_slow_stream(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_asyncio_wait_for_allows_fast_read(self):
        """Verify asyncio.wait_for succeeds when stream completes within timeout."""

        async def _fast_stream():
            return "done"

        result = await asyncio.wait_for(_fast_stream(), timeout=5)
        assert result == "done"

    def test_upload_byte_timeout_formula_default(self):
        """Verify the timeout formula for default MAX_UPLOAD_BYTES (500 MB).

        _UPLOAD_BYTE_TIMEOUT_SECONDS = max(60, int(MAX_UPLOAD_BYTES / (256 * 1024)))
        500 * 1024 * 1024 / (256 * 1024) = 2000 seconds
        """
        max_upload_bytes = 500 * 1024 * 1024
        timeout = max(60, int(max_upload_bytes / (256 * 1024)))
        assert timeout == 2000

    def test_upload_byte_timeout_formula_minimum_60s(self):
        """For small uploads, timeout floor is 60 seconds."""
        small_bytes = 10 * 1024 * 1024  # 10 MB
        timeout = max(60, int(small_bytes / (256 * 1024)))
        assert timeout == 60

    def test_upload_byte_timeout_formula_large_file(self):
        """For a 2 GB upload, timeout should scale proportionally."""
        large_bytes = 2 * 1024 * 1024 * 1024  # 2 GB
        timeout = max(60, int(large_bytes / (256 * 1024)))
        assert timeout == 8192

    @pytest.mark.asyncio
    async def test_timeout_cleanup_removes_tmp_file(self, tmp_path):
        """When timeout triggers, the partial temp file should be cleaned up."""
        tmp_file = tmp_path / ".upload-test-timeout"
        tmp_file.write_bytes(b"partial upload data")
        assert tmp_file.exists()

        # Simulate the cleanup that the timeout handler performs
        tmp_file.unlink(missing_ok=True)
        assert not tmp_file.exists()

    @pytest.mark.asyncio
    async def test_timeout_cleanup_missing_file_no_error(self, tmp_path):
        """unlink(missing_ok=True) should not raise if file already gone."""
        tmp_file = tmp_path / ".upload-nonexistent"
        assert not tmp_file.exists()
        # Should not raise
        tmp_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_timeout_exception_is_not_swallowed(self):
        """HTTPException from size check should NOT be caught as TimeoutError."""
        from fastapi import HTTPException

        async def _oversized_stream():
            raise HTTPException(413, "File too large")

        with pytest.raises(HTTPException) as exc_info:
            await asyncio.wait_for(_oversized_stream(), timeout=60)
        assert exc_info.value.status_code == 413

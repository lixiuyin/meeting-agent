import base64

import pytest
from fastapi import HTTPException

from src.api.dependencies import MAX_PAGE_OFFSET, decode_cursor, encode_cursor


@pytest.mark.parametrize("value", [-1, 2**63, 10**40, MAX_PAGE_OFFSET + 1])
def test_cursor_rejects_out_of_range(value):
    cursor = base64.urlsafe_b64encode(str(value).encode()).decode()
    with pytest.raises(HTTPException) as error:
        decode_cursor(cursor)
    assert error.value.status_code == 400


@pytest.mark.parametrize("cursor", ["%%%", "a", "-" * 100, "YWJj"])
def test_cursor_rejects_malformed(cursor):
    with pytest.raises(HTTPException):
        decode_cursor(cursor)


def test_cursor_roundtrip():
    for offset in (0, 10, MAX_PAGE_OFFSET):
        assert decode_cursor(encode_cursor(offset)) == offset
    assert decode_cursor(None) == 0

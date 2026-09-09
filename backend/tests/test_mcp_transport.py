"""MCP transport selection and network-boundary regressions."""

import pytest

from src.mcp import _resolve_http_port, _resolve_transport, _validate_http_binding


def test_http_port_selects_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.setenv("MCP_HTTP_PORT", "9001")
    assert _resolve_transport() == "streamable-http"
    assert _resolve_http_port() == 9001


def test_explicit_sse_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "sse")
    assert _resolve_transport() == "sse"


def test_network_http_binding_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        _validate_http_binding("streamable-http", "0.0.0.0")


def test_stdio_ignores_network_host() -> None:
    _validate_http_binding("stdio", "0.0.0.0")

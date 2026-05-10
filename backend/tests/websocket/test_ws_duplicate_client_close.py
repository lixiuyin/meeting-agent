"""Tests for WebSocket duplicate client handling."""

import pytest

from src.services.websocket import WebSocketManager


class FakeWebSocket:
    """Minimal WebSocket mock for testing."""

    def __init__(self):
        self.closed = False
        self.close_code = None
        self.close_reason = None
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def send_text(self, data: str):
        self.sent.append(data)


class TestWsDuplicateClientClose:
    @pytest.mark.asyncio
    async def test_first_connection_accepted(self):
        """First connection should be accepted normally."""
        manager = WebSocketManager()
        ws = FakeWebSocket()
        result = await manager.connect(ws, "client-1", "user-1")
        assert result is True
        assert ws.accepted is True

    @pytest.mark.asyncio
    async def test_duplicate_closes_old_connection(self):
        """Duplicate client_id should close the old connection."""
        manager = WebSocketManager()
        old_ws = FakeWebSocket()
        new_ws = FakeWebSocket()

        await manager.connect(old_ws, "client-1", "user-1")
        assert old_ws.accepted is True

        await manager.connect(new_ws, "client-1", "user-1")
        assert new_ws.accepted is True
        assert old_ws.closed is True
        assert old_ws.close_code == 1001

    @pytest.mark.asyncio
    async def test_different_users_same_client_id(self):
        """Different users with same client_id should work independently."""
        manager = WebSocketManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()

        await manager.connect(ws1, "client-1", "user-1")
        await manager.connect(ws2, "client-1", "user-2")
        # Both should be accepted since keys differ by user_id
        assert ws1.accepted is True
        assert ws2.accepted is True
        assert ws1.closed is False

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from src.main import app
from src.services.websocket import websocket_manager


def test_websocket_ping_pong_and_disconnect():
    # Clear stale catch-up buffer from prior tests
    websocket_manager._recent.clear()

    client = TestClient(app)
    assert len(websocket_manager._connections) == 0

    with client.websocket_connect("/api/v1/ws?client_id=e2e-ws&api_key=test-api-key") as ws:
        ws.send_text("ping")
        payload = json.loads(ws.receive_text())
        assert payload["type"] == "pong"
        assert any(cid == "e2e-ws" for _, cid in websocket_manager._connections)

    assert not any(cid == "e2e-ws" for _, cid in websocket_manager._connections)


def test_websocket_rejects_invalid_api_key():
    client = TestClient(app)
    with patch("src.api.routers.websocket.settings") as mock_settings:
        mock_settings.API_KEY = SecretStr("test-api-key")
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/api/v1/ws?client_id=bad&api_key=wrong-key"),
        ):
            pass


@pytest.mark.anyio
async def test_broadcast_with_concurrent_disconnect():
    from starlette.websockets import WebSocketState

    from src.services.websocket import WebSocketManager

    manager = WebSocketManager()

    class _FakeSocket:
        def __init__(self, on_send=None):
            self._on_send = on_send
            self.client_state = WebSocketState.CONNECTED

        async def send_text(self, _payload: str) -> None:
            if self._on_send:
                self._on_send()

    # Keys are (user_id, client_id) tuples
    key_a = ("user1", "a")
    key_b = ("user1", "b")
    manager._connections = {
        key_a: _FakeSocket(on_send=lambda: manager.disconnect("b")),
        key_b: _FakeSocket(),
    }

    await manager.broadcast({"type": "progress"})
    assert key_a in manager._connections

"""WebSocket manager for real-time notifications."""

import json
import logging
import threading
from collections import deque
from typing import Any

from fastapi import WebSocket

from ..core.tracing import otel_span
from ..models.schemas.websocket import (
    WSCatchUpMessage,
    WSCompleteMessage,
    WSProgressMessage,
    serialize_ws_message,
)

logger = logging.getLogger(__name__)

# M-CONC-3: Ring buffer of recent notifications for late-joining clients.
_MAX_RECENT_NOTIFICATIONS = 50


class WebSocketManager:
    """Manage WebSocket connections for real-time notifications.

    Connections are indexed by (user_id, client_id) to enforce per-user
    isolation.  Broadcasts are scoped to the meeting owner so users
    cannot observe other users' activity.
    """

    def __init__(self):
        # Map (user_id, client_id) -> WebSocket
        self._connections: dict[tuple[str, str], WebSocket] = {}
        self._lock = threading.Lock()
        # M-CONC-3: Recent notification buffer for catch-up delivery.
        self._recent: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT_NOTIFICATIONS)

    async def connect(self, websocket: WebSocket, client_id: str, user_id: str) -> bool:
        """Accept a new WebSocket connection and deliver recent notifications.

        If the (user_id, client_id) pair is already connected, the old
        connection is closed before the new one is accepted.
        """
        key = (user_id, client_id)
        old_ws: WebSocket | None = None
        with self._lock:
            if key in self._connections:
                old_ws = self._connections[key]
            self._connections[key] = websocket
            total = len(self._connections)
            # M-CONC-3: Snapshot recent notifications scoped to this user.
            catch_up = [e for e in self._recent if e.get("user_id") == user_id]
        if old_ws is not None:
            try:
                await old_ws.close(code=1001, reason="Replaced by new connection")
            except Exception:
                logger.debug("Failed to close old WS for %s", client_id, exc_info=True)
        await websocket.accept()
        logger.info(
            "WebSocket client connected: %s (user: %s, total: %d)",
            client_id,
            user_id,
            total,
        )
        # Deliver catch-up notifications so late-joining clients see recent events.
        if catch_up:
            try:
                await websocket.send_text(serialize_ws_message(WSCatchUpMessage(events=catch_up)))
            except Exception:
                logger.debug("Failed to deliver catch-up to %s", client_id, exc_info=True)
        return True

    def disconnect(
        self,
        client_id: str,
        *,
        user_id: str | None = None,
        websocket: WebSocket | None = None,
    ) -> None:
        """Remove only the intended connection.

        ``websocket`` protects a freshly replaced connection from the stale
        endpoint's eventual ``finally`` block. ``user_id`` prevents a client id
        collision from disconnecting a different principal.
        """
        with self._lock:
            keys_to_remove = [
                key
                for key, current in self._connections.items()
                if key[1] == client_id
                and (user_id is None or key[0] == user_id)
                and (websocket is None or current is websocket)
            ]
            for key in keys_to_remove:
                del self._connections[key]
            total = len(self._connections)
        if keys_to_remove:
            logger.info(
                "WebSocket client disconnected: %s (total: %d)",
                client_id,
                total,
            )

    def _disconnect_key(self, key: tuple[str, str]) -> None:
        """Remove a WebSocket connection by its full key."""
        with self._lock:
            if key in self._connections:
                del self._connections[key]

    async def send_message(
        self,
        client_id: str,
        message: dict[str, Any],
        *,
        user_id: str | None = None,
    ) -> None:
        """Send a message to a client, optionally scoped to its principal."""
        with self._lock:
            entry = next(
                (
                    (k, ws)
                    for k, ws in self._connections.items()
                    if k[1] == client_id and (user_id is None or k[0] == user_id)
                ),
                None,
            )
        if entry is None:
            return
        key, ws = entry
        try:
            with otel_span("ws.send_message", {"client_id": client_id}):
                await ws.send_text(json.dumps(message))
        except Exception as e:
            logger.error("Failed to send message to %s: %s", client_id, e)
            self._disconnect_key(key)

    async def broadcast_to_user(self, user_id: str, message: dict[str, Any]) -> None:
        """Broadcast a message to all connections belonging to a specific user."""
        import asyncio

        from starlette.websockets import WebSocketState

        with self._lock:
            user_conns = [(k, ws) for k, ws in self._connections.items() if k[0] == user_id]

        async def _send_one(key: tuple[str, str], ws: WebSocket) -> tuple[str, str] | None:
            try:
                if ws.client_state != WebSocketState.CONNECTED:
                    return key
                with otel_span("ws.broadcast.send", {"client_id": key[1]}):
                    await ws.send_text(json.dumps(message))
                return None
            except Exception as e:
                logger.debug("Failed to broadcast to %s: %s", key[1], e)
                return key

        results = await asyncio.gather(
            *[_send_one(k, ws) for k, ws in user_conns],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, tuple):
                self._disconnect_key(result)
            elif isinstance(result, Exception):
                logger.warning("Unexpected broadcast error: %s", result)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients.

        Uses asyncio.gather with return_exceptions=True so one client
        failure does not block delivery to other clients (CONC-3).
        """
        import asyncio

        from starlette.websockets import WebSocketState

        # Persist to recent buffer for late-joining clients (transient events
        # like heartbeats are excluded).
        if message.get("type") not in ("ping", "heartbeat"):
            self._recent.append(message)

        with self._lock:
            connections_snapshot = list(self._connections.items())

        async def _send_one(key: tuple[str, str], ws: WebSocket) -> tuple[str, str] | None:
            try:
                if ws.client_state != WebSocketState.CONNECTED:
                    return key
                with otel_span("ws.broadcast.send", {"client_id": key[1]}):
                    await ws.send_text(json.dumps(message))
                return None
            except Exception as e:
                logger.debug("Failed to broadcast to %s: %s", key[1], e)
                return key

        results = await asyncio.gather(
            *[_send_one(k, ws) for k, ws in connections_snapshot],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, tuple):
                self._disconnect_key(result)
            elif isinstance(result, Exception):
                logger.warning("Unexpected broadcast error: %s", result)

    async def notify_progress(
        self,
        meeting_id: int,
        status: str,
        progress: float,
        message: str = "",
        user_id: str | None = None,
    ) -> None:
        """Send processing progress update to the meeting owner."""
        payload = WSProgressMessage(
            meeting_id=meeting_id,
            status=status,
            progress=progress,
            message=message,
            user_id=user_id,
        )
        payload_dict = payload.model_dump(mode="json", exclude_none=True)
        if user_id:
            self._recent.append(payload_dict)
            await self.broadcast_to_user(user_id, payload_dict)
        else:
            await self.broadcast(payload_dict)

    async def notify_complete(
        self,
        meeting_id: int,
        status: str,
        title: str = "",
        user_id: str | None = None,
    ) -> None:
        """Notify the meeting owner that processing is complete."""
        payload = WSCompleteMessage(
            meeting_id=meeting_id,
            status=status,
            title=title,
            user_id=user_id,
        )
        payload_dict = payload.model_dump(mode="json", exclude_none=True)
        if user_id:
            self._recent.append(payload_dict)
            await self.broadcast_to_user(user_id, payload_dict)
        else:
            await self.broadcast(payload_dict)

    async def notify_meeting_update(
        self,
        meeting_id: int,
        data: dict[str, Any],
        user_id: str | None = None,
    ) -> None:
        """Send a generic meeting update notification."""
        payload = {
            "type": "meeting_update",
            "meeting_id": meeting_id,
            **data,
        }
        if user_id:
            payload["user_id"] = user_id
            self._recent.append(payload)
            await self.broadcast_to_user(user_id, payload)
        else:
            await self.broadcast(payload)


# Global WebSocket manager instance
websocket_manager = WebSocketManager()

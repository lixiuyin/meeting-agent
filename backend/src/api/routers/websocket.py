"""WebSocket API for real-time notifications."""

import asyncio
import base64
import hashlib
import hmac as hmac_mod
import logging
import re
import secrets
import time

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from pydantic import BaseModel

from ...api.middleware import limiter
from ...core.config import settings
from ...core.security import _derive_user_id_from_api_key, verify_api_key
from ...models.schemas.websocket import (
    WSEchoMessage,
    WSPingMessage,
    WSPongMessage,
    serialize_ws_message,
)
from ...services.websocket import websocket_manager

router = APIRouter(prefix="/ws", tags=["websocket"])
logger = logging.getLogger(__name__)

_WS_IDLE_TIMEOUT = 30  # seconds before sending a ping on inactivity
_WS_MAX_MISSED_PINGS = 2  # disconnect after this many unanswered pings
_WS_MAX_LIFETIME = 3600  # absolute connection limit: 1 hour
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,128}$")

# Short-lived WS token TTL and signing
_WS_TOKEN_TTL = 300  # 5 minutes
_dev_ws_signing_key: str | None = None


def _derive_ws_signing_key() -> bytes:
    """Derive a signing key from the configured API key for WS tokens.

    In dev mode (no API key), a per-startup random key is generated.
    """
    global _dev_ws_signing_key
    key = settings.API_KEY.get_secret_value()
    if not key:
        if settings.ENVIRONMENT == "dev":
            if _dev_ws_signing_key is None:
                _dev_ws_signing_key = secrets.token_hex(32)
            key = _dev_ws_signing_key
        else:
            raise RuntimeError("API_KEY must be configured for WS token signing")
    return hashlib.sha256(f"ws-token:{key}".encode()).digest()


def _generate_ws_token(user_id: str = "default") -> str:
    """Generate a short-lived HMAC token for WebSocket auth."""
    key = _derive_ws_signing_key()
    expiry = int(time.time()) + _WS_TOKEN_TTL
    message = f"ws:{user_id}:{expiry}".encode()
    signature = hmac_mod.new(key, message, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{token}.{expiry}"


def _validate_ws_token(token: str, user_id: str = "default") -> bool:
    """Validate a short-lived WS token."""
    if not token or "." not in token:
        return False
    sig_b64, _, expiry_str = token.rpartition(".")
    if not sig_b64 or not expiry_str:
        return False
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    if time.time() > expiry:
        return False
    key = _derive_ws_signing_key()
    payload = f"ws:{user_id}:{expiry}".encode()
    expected = hmac_mod.new(key, payload, hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    return hmac_mod.compare_digest(sig_b64, expected_b64)


def _derive_ws_user_id(api_key: str | None) -> str:
    """Derive a user_id from the API key (same logic as core.security)."""
    configured_key = settings.API_KEY.get_secret_value()
    if not configured_key:
        return "default"
    if not api_key:
        return "anonymous"
    return _derive_user_id_from_api_key(api_key)


def _verify_ws_auth(
    api_key: str | None,
    token: str | None,
) -> tuple[bool, str]:
    """Validate WS auth via API key header or short-lived token.

    Returns (is_valid, user_id).
    """
    configured_key = settings.API_KEY.get_secret_value()
    if not configured_key:
        # Dev mode: allow direct access. Validate token if provided.
        if token:
            if _validate_ws_token(token, user_id="default"):
                return True, "default"
            return False, "anonymous"
        return True, "default"
    # Production currently accepts one configured proxy key. Both HTTP and
    # WebSocket paths must derive the exact same principal from that key.
    configured_user_id = _derive_user_id_from_api_key(configured_key)
    if api_key and hmac_mod.compare_digest(api_key, configured_key):
        return True, configured_user_id
    if token and _validate_ws_token(token, user_id=configured_user_id):
        return True, configured_user_id
    return False, "anonymous"


class WSTokenResponse(BaseModel):
    token: str


@router.post("/token", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def create_ws_token(
    request: Request,
    principal: dict[str, str] = Depends(verify_api_key),
) -> WSTokenResponse:
    """Generate a short-lived token for WebSocket authentication.

    Requires X-API-Key header. The returned token is valid for 5 minutes
    and can be used as a ``?token=`` query parameter for WebSocket connections,
    avoiding exposure of the API key in URLs.
    """
    return WSTokenResponse(token=_generate_ws_token(user_id=principal["user_id"]))


@router.websocket("")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str = Query(..., description="Unique client identifier"),
    api_key: str | None = Query(None, description="API key (deprecated, use token)"),
    token: str | None = Query(None, description="Short-lived WS token from POST /ws/token"),
):
    """WebSocket endpoint for real-time notifications.

    Connect with: ws://host/api/v1/ws?client_id=your_client_id&token=...
    Legacy: api_key query param still accepted but token is preferred.

    Protocol:
    - Client sends "ping" → Server responds with {"type":"pong"}
    - Server sends {"type":"ping"} on idle → Client should respond with "pong"
    - Connection closed after 2 unanswered server pings

    Message types:
    - progress: Processing progress updates
    - complete: Processing completion notification
    """
    if not _CLIENT_ID_RE.match(client_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid client_id")
        return

    valid, user_id = _verify_ws_auth(api_key, token)
    if not valid:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid authentication",
        )

    accepted = await websocket_manager.connect(websocket, client_id, user_id)
    if not accepted:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="client_id already in use",
        )
        return

    missed_pings = 0
    connected_at = time.monotonic()

    try:
        while True:
            if time.monotonic() - connected_at > _WS_MAX_LIFETIME:
                logger.info("WebSocket client %s reached max lifetime, disconnecting", client_id)
                websocket_manager.disconnect(client_id, user_id=user_id, websocket=websocket)
                await websocket.close(code=1001, reason="Max lifetime reached")
                break
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=_WS_IDLE_TIMEOUT)
            except TimeoutError:
                # No message received within idle timeout — send a ping
                missed_pings += 1
                if missed_pings > _WS_MAX_MISSED_PINGS:
                    logger.info(
                        "WebSocket client %s missed %d pings, disconnecting",
                        client_id,
                        missed_pings,
                    )
                    websocket_manager.disconnect(client_id, user_id=user_id, websocket=websocket)
                    await websocket.close(code=1000, reason="Idle timeout")
                    break
                await websocket.send_text(serialize_ws_message(WSPingMessage()))
                continue

            missed_pings = 0  # reset on any received message

            # Client-initiated ping
            if data == "ping":
                await websocket.send_text(serialize_ws_message(WSPongMessage()))
                continue

            # Client pong response to our ping
            if data == "pong":
                continue

            logger.debug("Received message from %s: %s", client_id, data)
            await websocket.send_text(serialize_ws_message(WSEchoMessage(data=data)))

    except WebSocketDisconnect:
        logger.debug("WebSocket client %s disconnected", client_id)
    except Exception as e:
        logger.error("WebSocket error for %s: %s", client_id, e, exc_info=True)
    finally:
        websocket_manager.disconnect(client_id, user_id=user_id, websocket=websocket)

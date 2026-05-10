"""WebSocket message Pydantic models for typed outbound validation."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class WSPingMessage(BaseModel):
    """Server-initiated ping to check client liveness."""

    type: Literal["ping"] = "ping"


class WSPongMessage(BaseModel):
    """Response to a client-initiated ping."""

    type: Literal["pong"] = "pong"


class WSEchoMessage(BaseModel):
    """Echo back an arbitrary client message."""

    type: Literal["echo"] = "echo"
    data: str


class WSProgressMessage(BaseModel):
    """Processing progress notification."""

    type: Literal["progress"] = "progress"
    meeting_id: int
    status: str
    progress: float = Field(ge=0.0, le=1.0)
    message: str = ""
    user_id: str | None = None


class WSCompleteMessage(BaseModel):
    """Processing completion notification."""

    type: Literal["complete"] = "complete"
    meeting_id: int
    status: str
    title: str = ""
    user_id: str | None = None


class WSErrorMessage(BaseModel):
    """Error notification."""

    type: Literal["error"] = "error"
    meeting_id: int | None = None
    message: str
    code: str | None = None


class WSCatchUpMessage(BaseModel):
    """Catch-up message for late-joining clients."""

    type: Literal["catch_up"] = "catch_up"
    events: list[dict[str, Any]]


WSMessage = (
    WSPingMessage
    | WSPongMessage
    | WSEchoMessage
    | WSProgressMessage
    | WSCompleteMessage
    | WSErrorMessage
    | WSCatchUpMessage
)


def serialize_ws_message(msg: WSMessage) -> str:
    """Validate and serialize a WS message to JSON string."""
    import json

    return json.dumps(msg.model_dump(mode="json", exclude_none=True))

"""WebSocket endpoint for realtime inbox updates."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["WebSocket"])


@router.websocket("/{store_id}")
async def inbox_websocket(
    websocket: WebSocket,
    store_id: str,
    token: Annotated[str, Query(...)],
) -> None:
    """WebSocket for realtime inbox updates.

    Connect with: wss://host/ws/inbox/{store_id}?token={jwt}

    Events sent:
    - new_message: new inbound message in thread
    - thread_updated: thread status changed
    - connection_status: channel connection status changed
    """
    from src.infrastructure.realtime.redis_pubsub import RealtimePublisher

    try:
        payload = _verify_token(token)
        if not payload or payload.get("store_id") != store_id:
            await websocket.close(code=4003, reason="Invalid token")
            return
    except Exception:
        await websocket.close(code=4003, reason="Invalid token")
        return

    await websocket.accept()

    publisher = RealtimePublisher()
    channel = f"store:{store_id}:inbox"
    pubsub = await publisher.subscribe(channel)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=30.0
            )
            if message:
                raw = message.get("data")
                if isinstance(raw, bytes | bytearray):
                    raw = raw.decode("utf-8")
                try:
                    data = json.loads(raw) if isinstance(raw, str) else raw
                except (ValueError, TypeError):
                    data = {"raw": raw}
                await websocket.send_json(data)
    except WebSocketDisconnect:
        pass
    finally:
        await publisher.unsubscribe(channel, pubsub)


def _verify_token(token: str) -> dict[str, Any] | None:
    """Verify JWT token for websocket auth."""
    from src.infrastructure.external_services.token_service import TokenService

    try:
        service = TokenService()
        payload = service.verify_token(token)
        return {
            "user_id": str(payload.user_id),
            "email": payload.email,
            "role": payload.role,
            "tenant_id": str(payload.tenant_id) if payload.tenant_id else None,
            "store_id": None,
        }
    except Exception:
        return None


__all__ = ["router"]

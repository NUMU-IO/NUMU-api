"""WebSocket endpoint for realtime inbox updates."""

import json
from typing import Annotated, Any
from uuid import UUID

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
        if not payload or not await _user_may_access_store(payload, store_id):
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
    """Verify JWT token for websocket auth.

    Access tokens are USER-scoped (no store claim), so store access is
    checked separately against the DB in ``_user_may_access_store``.
    The previous version compared a hard-coded ``store_id: None`` to the
    path param, which closed every connection with 4003.
    """
    from src.infrastructure.external_services.token_service import TokenService

    try:
        service = TokenService()
        payload = service.verify_token(token)
        return {
            "user_id": str(payload.user_id),
            "email": payload.email,
            "role": payload.role,
            "tenant_id": str(payload.tenant_id) if payload.tenant_id else None,
        }
    except Exception:
        return None


async def _user_may_access_store(payload: dict[str, Any], store_id: str) -> bool:
    """Owner of the store, or a member of its tenant, or a super admin."""
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    try:
        sid = UUID(store_id)
    except ValueError:
        return False
    role = str(payload.get("role") or "").lower()
    if role in {"super_admin", "superadmin"}:
        return True
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(StoreModel.owner_id, StoreModel.tenant_id).where(
                    StoreModel.id == sid
                )
            )
        ).first()
    if row is None:
        return False
    owner_id, tenant_id = row
    if str(owner_id) == payload.get("user_id"):
        return True
    return bool(payload.get("tenant_id")) and str(tenant_id) == payload.get("tenant_id")


__all__ = ["router"]

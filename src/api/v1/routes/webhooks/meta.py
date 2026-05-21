"""Meta (Facebook/Instagram/WhatsApp) webhook receiver."""

import time

from fastapi import APIRouter, Query, status
from fastapi.responses import PlainTextResponse

from src.config.logging_config import get_logger
from src.infrastructure.external_services.meta.signature import (
    verify_meta_webhook,
    verify_x_hub_signature,
)

logger = get_logger(__name__)

router = APIRouter(tags=["Webhooks - Meta"])


@router.get("")
async def verify_webhook(
    mode: str = Query(..., description="Webhook mode"),
    verify_token: str = Query(..., description="Verify token"),
) -> PlainTextResponse:
    """Verify webhook subscription - Meta calls this with GET."""
    challenge = verify_meta_webhook(mode=mode, token=verify_token, challenge="")
    if challenge is not None:
        return PlainTextResponse(content=challenge, status_code=status.HTTP_200_OK)
    return PlainTextResponse(
        content="Verification failed", status_code=status.HTTP_403_FORBIDDEN
    )


@router.post("")
async def receive_webhook(
    payload: dict,
    mode: str = Query(...),
    verify_token: str = Query(...),
    x_hub_signature: str | None = Query(None, alias="X-Hub-Signature-256"),
) -> PlainTextResponse:
    """Receive all Meta webhook events (Facebook, Instagram, WhatsApp)."""
    import json
    import uuid

    from src.infrastructure.webhooks.meta_handlers import (
        handle_message_status_webhook,
        handle_message_webhook,
    )

    start_time = time.perf_counter()
    event_id = str(uuid.uuid4())

    if x_hub_signature and not verify_x_hub_signature(
        json.dumps(payload),
        x_hub_signature,
    ):
        return PlainTextResponse(
            content="Invalid signature", status_code=status.HTTP_403_FORBIDDEN
        )

    entry = payload.get("entry", [])
    for e in entry:
        messaging = e.get("messaging", [])
        for msg in messaging:
            message = msg.get("message", {})
            delivery = msg.get("delivery")

            if message and not delivery:
                await handle_message_webhook(msg)
            elif delivery:
                await handle_message_status_webhook(msg)

        changes = e.get("changes", [])
        for change in changes:
            field = change.get("field")
            value = change.get("value", {})
            if field == "messages":
                await handle_message_webhook(value)
            elif field == "messages_status":
                await handle_message_status_webhook(value)

    latency_ms = (time.perf_counter() - start_time) * 1000
    logger.info(
        "meta_webhook_received",
        event_id=event_id,
        channel="meta",
        latency_ms=round(latency_ms, 2),
        entry_count=len(entry),
    )

    return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK)


__all__ = ["router"]

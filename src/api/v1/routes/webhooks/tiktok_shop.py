"""TikTok Shop webhook receiver.

Receives TikTok Shop Open-Platform push notifications (ORDER_STATUS_CHANGE,
etc.). Verifies the HMAC ``Authorization`` header against the app secret, then
enqueues an ingestion task per order — never blocking on the DB / TikTok fetch
so the webhook always ACKs fast (TikTok retries on non-2xx).

DORMANT until ``NUMU_TIKTOK_SHOP_APP_SECRET`` is set: without app creds the
signature can't be verified, so every call is rejected 401.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from src.config.logging_config import get_logger
from src.infrastructure.external_services.tiktok.shop_client import TikTokShopClient

logger = get_logger(__name__)

router = APIRouter(tags=["Webhooks - TikTok Shop"])

# TikTok Shop webhook ``type`` values that carry an order we want to ingest.
# 1 = ORDER_STATUS_CHANGE in the numeric schema; we also accept the string.
_ORDER_EVENT_TYPES = {"ORDER_STATUS_CHANGE", "1", 1}


@router.post("")
async def receive_tiktok_shop_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Verify + dispatch a TikTok Shop webhook.

    Always returns 200 once the signature is valid (even for event types we
    ignore) so TikTok doesn't retry. 401 on signature failure.
    """
    raw = await request.body()

    client = TikTokShopClient()
    if not client.verify_webhook_signature(raw_body=raw, signature=authorization or ""):
        logger.warning("tiktok_shop_webhook_bad_signature")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"ok": False, "error": "invalid_signature"},
        )

    import json

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})

    event_type = payload.get("type")
    if event_type not in _ORDER_EVENT_TYPES:
        # Signed but not an order event — ACK and ignore.
        return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})

    shop_id = str(payload.get("shop_id") or "")
    data = payload.get("data") or {}
    order_id = str(data.get("order_id") or data.get("id") or "")

    if shop_id and order_id:
        try:
            from src.infrastructure.messaging.tasks.tiktok_shop_tasks import (
                tiktok_shop_ingest_order,
            )

            tiktok_shop_ingest_order.delay(shop_id=shop_id, order_id=order_id)
        except Exception:
            # Never fail the webhook on an enqueue error — TikTok would retry
            # and we'd rather log + move on (the merchant can re-sync).
            logger.exception(
                "tiktok_shop_webhook_enqueue_failed",
                extra={"shop_id": shop_id, "order_id": order_id},
            )

    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})

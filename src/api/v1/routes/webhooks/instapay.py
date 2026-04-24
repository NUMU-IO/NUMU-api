"""Webhook prefix for future PSP-mediated InstaPay callbacks.

There is no webhook source in the MVP — the manual proof-upload flow is
the authoritative confirmation path. This module exists so the route
prefix is stable *before* Paymob (or a bank) ships its InstaPay SKU,
so we don't have to coordinate a storefront redirect URL change on the
day we switch. The storefront never sees this; it's advertised to
providers only.

When a provider ships:
  1. Add credential load + signature verification at the top.
  2. Look up the order via the reference code (already the join key —
     see :class:`InstapayIntentRepository.get_by_reference_code`).
  3. Call ``order.mark_as_paid(...)`` + publish ``OrderPaidEvent``
     (same downstream as the manual-review approve path).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from src.config.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post("/callback", operation_id="instapay_webhook_callback")
async def instapay_webhook_callback(request: Request) -> JSONResponse:
    """Placeholder for future PSP callbacks.

    Returns 501 so a misconfigured integration surfaces clearly instead
    of silently swallowing events. Logs the body for diagnostics.
    """
    try:
        payload = await request.body()
        logger.warning(
            "instapay_webhook_not_implemented",
            byte_len=len(payload),
            source_ip=request.client.host if request.client else None,
        )
    except Exception:
        logger.exception("instapay_webhook_body_read_failed")

    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "status": "not_implemented",
            "detail": (
                "InstaPay webhooks are not yet wired; confirmations "
                "happen via the proof-upload flow."
            ),
        },
    )

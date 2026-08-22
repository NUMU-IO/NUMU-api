"""Paymob proof verification for Shopify payment-link completion.

``POST /shopify/payment-links/{id}/complete`` is a public endpoint (the
buyer-facing payment page has no credentials), so completion must carry
proof. This module verifies a Paymob transaction-processed callback
payload against the merchant's own HMAC secret (stored encrypted at
``store.settings.payment.paymob``) and cross-checks the paid amount
against the payment session — a session UUID alone must never be enough
to mark a COD order as paid.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Machine-readable rejection reasons (route maps them to status codes).
REASON_NOT_CONFIGURED = "paymob_not_configured"
REASON_INVALID_SIGNATURE = "invalid_signature"
REASON_NOT_SUCCESSFUL = "transaction_not_successful"
REASON_AMOUNT_MISMATCH = "amount_mismatch"


async def verify_paymob_completion(
    session: AsyncSession,
    *,
    store_id: UUID,
    expected_amount_cents: int,
    paymob_payload: dict,
    paymob_hmac: str,
) -> tuple[bool, str]:
    """Validate a Paymob callback as proof of payment for a session.

    Returns ``(accepted, reason)``. Accepted requires ALL of:
    1. the store has Paymob credentials configured,
    2. the payload's HMAC-SHA512 verifies against the store's secret,
    3. ``obj.success`` is true,
    4. ``obj.amount_cents`` equals the payment session's amount.
    """
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.external_services.paymob.payment_service import (
        PaymentError,
        PaymobPaymentService,
        get_merchant_paymob_credentials,
    )

    store_q = await session.execute(select(StoreModel).where(StoreModel.id == store_id))
    store = store_q.scalar_one_or_none()
    if store is None:
        return False, REASON_NOT_CONFIGURED

    try:
        creds = await get_merchant_paymob_credentials(store.settings or {})
    except PaymentError:
        return False, REASON_NOT_CONFIGURED
    hmac_secret = creds.get("hmac_secret")
    if not hmac_secret:
        return False, REASON_NOT_CONFIGURED

    verifier = PaymobPaymentService(hmac_secret=hmac_secret)
    verified = verifier.verify_webhook_signature(
        json.dumps(paymob_payload).encode(), paymob_hmac
    )
    if verified is None:
        return False, REASON_INVALID_SIGNATURE

    obj = paymob_payload.get("obj") or {}
    if obj.get("success") is not True:
        return False, REASON_NOT_SUCCESSFUL

    try:
        paid_cents = int(obj.get("amount_cents", 0))
    except (TypeError, ValueError):
        paid_cents = 0
    if paid_cents != expected_amount_cents:
        logger.warning(
            "paymob completion amount mismatch: paid=%s expected=%s store=%s",
            paid_cents,
            expected_amount_cents,
            store_id,
        )
        return False, REASON_AMOUNT_MISMATCH

    return True, "ok"

"""Shared COD-to-prepaid WhatsApp nudge send for the Shopify app.

One implementation behind every Shopify-app send surface:

- the ``whatsapp_confirm`` automation action (``tasks.send_whatsapp_nudge``),
- the dashboard's manual "WhatsApp confirm" risk action,
- the Flow action's ``resend-verification`` endpoint, and
- the recovery-ladder step sends (``tasks.recovery.send_step``).

The message is the Meta-approved ``cod_recovery_offer_v1`` template — the
only platform template whose URL button lands on a pay page. (The
``order_confirmation_v3`` template historically used here has no
payment-link placeholder, so those sends failed Meta's placeholder
check with #132012.)

The URL button carries the suffix ``shopify/{session_id}``; the apex
redirector must map ``<template base>/shopify/<uuid>`` →
``{shopify_payment_page_base}/<uuid>`` (same nginx file as the /o/ and
/cart/ CTA redirects).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_EXPIRY_HOURS = 24

# Meta rejects blank template variables, so every send needs a non-empty
# promo line even when the merchant configured none.
DEFAULT_PROMO = {
    "en": "Pay online to secure your order.",
    "ar": "ادفع أونلاين لتأكيد طلبك.",
}


@dataclass
class NudgeSendResult:
    sent: bool
    session_id: str
    payment_url: str
    message_id: str | None = None
    error: str | None = None


def payment_page_url(session_id: str | UUID) -> str:
    """Public buyer-facing URL for a payment link session."""
    return f"{get_settings().shopify_payment_page_base()}/{session_id}"


async def create_payment_link_session(
    session,
    *,
    store_id: UUID,
    shopify_order_id: str,
    amount_cents: int,
    currency: str,
    expiry_hours: int = _DEFAULT_EXPIRY_HOURS,
):
    """Insert a payment_link_sessions row (flushed, not committed).

    Returns the model; the caller owns the commit so the row stays atomic
    with whatever else the caller persists (e.g. a recovery step).
    """
    from src.infrastructure.database.models.tenant.payment_link_session import (
        PaymentLinkSessionModel,
    )

    model = PaymentLinkSessionModel(
        store_id=store_id,
        shopify_order_id=shopify_order_id,
        amount_cents=amount_cents,
        currency=currency,
        available_gateways=["paymob"],
        expires_at=datetime.now(UTC) + timedelta(hours=expiry_hours),
    )
    session.add(model)
    await session.flush()
    return model


async def send_conversion_nudge(
    *,
    phone: str,
    customer_name: str,
    order_number: str,
    store_name: str,
    amount_cents: int,
    currency: str,
    payment_session_id: str,
    language: str = "ar",
    promo: str | None = None,
    wa_service=None,
) -> NudgeSendResult:
    """Send the pay-online offer for an existing payment link session.

    Pure network step — no DB access. ``wa_service`` is injectable for
    tests; defaults to the platform Meta transport.
    """
    from src.core.interfaces.services.messaging_service import (
        MessageContent,
        MessageRecipient,
        MessageType,
    )
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    lang = "ar" if str(language).lower().startswith("ar") else "en"
    pay_url = payment_page_url(payment_session_id)

    wa = wa_service or WhatsAppMessagingService()
    if not wa.enabled:
        return NudgeSendResult(
            sent=False,
            session_id=payment_session_id,
            payment_url=pay_url,
            error="whatsapp_disabled",
        )

    content = MessageContent(
        type=MessageType.COD_RECOVERY_OFFER,
        recipient=MessageRecipient(
            phone=phone,
            name=customer_name or "Customer",
            language=lang,
        ),
        template_params={
            "customer_name": customer_name or "Customer",
            "order_number": order_number or "-",
            "store_name": store_name or "your store",
            "total": f"{amount_cents / 100:,.2f} {currency}",
            "promo": (promo or "").strip() or DEFAULT_PROMO[lang],
            # URL button suffix — resolved by the apex /pay redirector.
            "pay_payload": f"shopify/{payment_session_id}",
        },
    )
    result = await wa.send_message(content)
    if result.success:
        logger.info(
            "shopify_nudge_sent session=%s message_id=%s",
            payment_session_id,
            result.message_id,
        )
    else:
        logger.warning(
            "shopify_nudge_failed session=%s error=%s",
            payment_session_id,
            result.error_message,
        )
    return NudgeSendResult(
        sent=result.success,
        session_id=payment_session_id,
        payment_url=pay_url,
        message_id=result.message_id,
        error=None if result.success else (result.error_message or "send_failed"),
    )


def store_display_name(shopify_domain: str | None, shop_name: str | None = None) -> str:
    """Best available merchant-facing store name for message copy."""
    if shop_name:
        return shop_name
    if shopify_domain:
        return shopify_domain.split(".")[0]
    return "your store"

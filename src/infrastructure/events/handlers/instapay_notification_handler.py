"""Customer notification handlers for InstaPay proof lifecycle events.

Four handlers — two per event, email + WhatsApp — so a failure on one
channel doesn't swallow the other. Each opens its own DB session with
RLS narrowed to the event's tenant, looks up the customer (for email /
phone / name) and the store (for display name + language), then calls
the respective messaging service. Independent from the invoice handler
so a PDF / templating failure doesn't swallow the payment-received
confirmation.

The retry URL passed to the rejection email points at the storefront's
InstaPay page for that order, computed from the store's own custom
domain or subdomain.
"""

from __future__ import annotations

from src.config.logging_config import get_logger
from src.core.events.payment_events import (
    PaymentProofApprovedEvent,
    PaymentProofRejectedEvent,
)
from src.core.interfaces.services.messaging_service import MessageRecipient
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.external_services.resend.email_service import (
    ResendEmailService,
)
from src.infrastructure.repositories.customer_repository import CustomerRepository
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.tenancy.rls import (
    enable_rls_bypass,
    narrow_to_tenant,
)

logger = get_logger(__name__)


def _storefront_base_url(store) -> str:
    """Return the storefront URL root for retry / tracking links.

    Prefers the merchant's custom domain, falls back to the NUMU
    subdomain. Kept in sync with the checkout.py convention so links
    line up with what the customer already sees.
    """
    if getattr(store, "custom_domain", None):
        return f"https://{store.custom_domain}"
    if getattr(store, "subdomain", None):
        return f"https://{store.subdomain}.numueg.app"
    return "https://numueg.app"


async def _load_customer_and_store(tenant_id, store_id, customer_id):
    """Resolve (customer, store) for an event, under the event's tenant.

    Returns ``(customer, store)`` — either may be ``None`` if a race
    deleted the row or the IDs were malformed.
    """
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        await narrow_to_tenant(session, tenant_id)
        store = await StoreRepository(session).get_by_id(store_id)
        customer = await CustomerRepository(session).get_by_id(customer_id)
        return customer, store


async def handle_payment_proof_approved(event: PaymentProofApprovedEvent) -> None:
    """Send the customer a short "payment received" email.

    Intentionally separate from ``handle_invoice_on_order_paid`` — that
    one mails the PDF and can fail (templating, fonts, ETA simulation);
    this one is a three-line HTML confirmation that mustn't.
    """
    log = logger.bind(
        event_id=str(event.event_id),
        order_id=str(event.order_id),
        proof_id=str(event.proof_id),
    )
    try:
        customer, store = await _load_customer_and_store(
            event.tenant_id, event.store_id, event.customer_id
        )
    except Exception:
        log.exception("instapay_approve_email_lookup_failed")
        return

    customer_email = (
        str(customer.email) if customer and getattr(customer, "email", None) else None
    )
    if not customer_email:
        log.info("instapay_approve_email_skip_no_email")
        return

    customer_name = getattr(customer, "full_name", None) if customer else None
    store_name = store.name if store else "NUMU"
    language = getattr(store, "default_language", "ar") if store else "ar"

    try:
        svc = ResendEmailService()
        await svc.send_instapay_payment_confirmed(
            email=customer_email,
            order_number=event.order_number,
            reference_code=event.reference_code,
            amount_cents=event.amount_cents,
            currency=event.currency,
            store_name=store_name,
            customer_name=customer_name,
            language=language,
            store_id=event.store_id,
            tenant_id=event.tenant_id,
        )
        log.info("instapay_approve_email_sent")
    except Exception:
        log.exception("instapay_approve_email_failed")


async def handle_payment_proof_rejected(event: PaymentProofRejectedEvent) -> None:
    """Send the customer a rejection email with reason + retry CTA."""
    log = logger.bind(
        event_id=str(event.event_id),
        order_id=str(event.order_id),
        proof_id=str(event.proof_id),
    )
    try:
        customer, store = await _load_customer_and_store(
            event.tenant_id, event.store_id, event.customer_id
        )
    except Exception:
        log.exception("instapay_reject_email_lookup_failed")
        return

    customer_email = (
        str(customer.email) if customer and getattr(customer, "email", None) else None
    )
    if not customer_email:
        log.info("instapay_reject_email_skip_no_email")
        return

    customer_name = getattr(customer, "full_name", None) if customer else None
    store_name = store.name if store else "NUMU"
    language = getattr(store, "default_language", "ar") if store else "ar"

    retry_url = event.retry_url
    if retry_url is None and store is not None and event.can_retry:
        retry_url = f"{_storefront_base_url(store)}/instapay/{event.order_id}"

    try:
        svc = ResendEmailService()
        await svc.send_instapay_payment_rejected(
            email=customer_email,
            order_number=event.order_number,
            reason=event.rejection_reason,
            can_retry=event.can_retry,
            retry_url=retry_url,
            store_name=store_name,
            customer_name=customer_name,
            language=language,
            store_id=event.store_id,
            tenant_id=event.tenant_id,
        )
        log.info("instapay_reject_email_sent", can_retry=event.can_retry)
    except Exception:
        log.exception("instapay_reject_email_failed")


# ── WhatsApp handlers ─────────────────────────────────────────────────
#
# Separate from the email handlers so a delivery failure on one channel
# doesn't block the other. Using the per-store resolver so each merchant
# can have their own verified Business number configured.


async def _resolve_whatsapp_service(store_id):
    """Return a per-store WhatsApp service, falling back to global."""
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    try:
        from src.infrastructure.external_services.whatsapp import (
            get_whatsapp_service,
        )

        async with AsyncSessionLocal() as session:
            return await get_whatsapp_service(store_id, session)
    except Exception:
        return WhatsAppMessagingService()


async def handle_whatsapp_payment_proof_approved(
    event: PaymentProofApprovedEvent,
) -> None:
    """Send a WhatsApp payment-received confirmation.

    Uses the template ``send_payment_received`` which is approved by
    Meta for out-of-session customer notifications (outside the 24h
    service window). Email is the authoritative channel; this fires
    best-effort and never raises.
    """
    log = logger.bind(
        event_id=str(event.event_id),
        order_id=str(event.order_id),
        proof_id=str(event.proof_id),
    )
    try:
        customer, store = await _load_customer_and_store(
            event.tenant_id, event.store_id, event.customer_id
        )
    except Exception:
        log.exception("instapay_approve_whatsapp_lookup_failed")
        return

    phone = (
        str(customer.phone) if customer and getattr(customer, "phone", None) else None
    )
    if not phone:
        return

    language = getattr(store, "default_language", "ar") if store else "ar"
    customer_name = getattr(customer, "full_name", None) if customer else None

    try:
        svc = await _resolve_whatsapp_service(event.store_id)
        amount = f"{event.amount_cents / 100:,.2f} {event.currency}"
        result = await svc.send_payment_received(
            recipient=MessageRecipient(
                phone=phone, name=customer_name or "", language=language
            ),
            order_number=event.order_number,
            amount=amount,
        )
        log.info(
            "instapay_approve_whatsapp_sent",
            success=result.success if result else False,
        )
    except Exception:
        log.exception("instapay_approve_whatsapp_failed")


async def handle_whatsapp_payment_proof_rejected(
    event: PaymentProofRejectedEvent,
) -> None:
    """Best-effort WhatsApp rejection nudge.

    Uses ``send_text_message``, which requires an open 24h customer
    service window. The recent proof upload often keeps the window
    open, but we do not guarantee delivery — email is the primary
    channel for rejections and always fires independently.
    """
    log = logger.bind(
        event_id=str(event.event_id),
        order_id=str(event.order_id),
        proof_id=str(event.proof_id),
    )
    try:
        customer, store = await _load_customer_and_store(
            event.tenant_id, event.store_id, event.customer_id
        )
    except Exception:
        log.exception("instapay_reject_whatsapp_lookup_failed")
        return

    phone = (
        str(customer.phone) if customer and getattr(customer, "phone", None) else None
    )
    if not phone:
        return

    language = getattr(store, "default_language", "ar") if store else "ar"
    is_ar = language == "ar"
    retry_line = ""
    if event.can_retry and store is not None:
        retry_url = (
            event.retry_url
            or f"{_storefront_base_url(store)}/instapay/{event.order_id}"
        )
        retry_line = (
            f"\nارفع إثبات جديد: {retry_url}"
            if is_ar
            else f"\nUpload a new proof: {retry_url}"
        )

    body = (
        f"تعذر تأكيد دفعتك للطلب #{event.order_number}.\nالسبب: "
        f"{event.rejection_reason}{retry_line}"
        if is_ar
        else (
            f"We couldn't confirm your payment for order "
            f"#{event.order_number}.\nReason: "
            f"{event.rejection_reason}{retry_line}"
        )
    )

    try:
        svc = await _resolve_whatsapp_service(event.store_id)
        result = await svc.send_text_message(phone=phone, text=body)
        log.info(
            "instapay_reject_whatsapp_sent",
            success=result.success if result else False,
        )
    except Exception:
        log.exception("instapay_reject_whatsapp_failed")

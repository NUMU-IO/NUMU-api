"""Confirm a COD order from an inbound WhatsApp quick-reply tap.

Wired from the WhatsApp webhook: when a customer taps the "Confirm" button
on an ``order_confirmation_request_v1`` message, Meta echoes the button
payload (``<subdomain>/<order_id>``) back to us. This resolves that payload
to the order and moves it PENDING → CONFIRMED.
"""

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.order import OrderStatus
from src.core.events.order_events import OrderStatusChangedEvent
from src.core.logging import get_logger

logger = get_logger(__name__)


def _parse_order_id(payload: str) -> UUID | None:
    """Extract the order UUID from a quick-reply payload.

    Accepts ``<action>:<subdomain>/<order_id>``, ``<subdomain>/<order_id>``,
    or a bare ``<order_id>``. The order UUID is always the segment after the
    last ``/`` (or the whole string when there is no ``/``), so an optional
    ``<action>:`` prefix doesn't interfere. Returns None when unparseable.
    """
    if not payload:
        return None
    candidate = payload.rsplit("/", 1)[-1].strip()
    # Bare ``<action>:<order_id>`` (no subdomain) — strip the action prefix.
    if "/" not in payload and ":" in candidate:
        candidate = candidate.split(":", 1)[-1].strip()
    try:
        return UUID(candidate)
    except ValueError:
        return None


# The quick-reply actions carried in template button payloads:
# - confirm/postpone/cancel — COD confirm-request (order_confirmation_request_v2)
# - shipall — merchant ship-digest "All shipped" (cod_ship_digest_v1,
#   payload id segment = DIGEST id, not an order id)
# - dlvyes/dlvnot/dlvref — customer delivery check (order_delivery_check_v1)
_VALID_ACTIONS = {
    "confirm",
    "postpone",
    "cancel",
    "shipall",
    "dlvyes",
    "dlvnot",
    "dlvref",
}


def parse_quick_reply_action(payload: str) -> str:
    """Return the action encoded in a quick-reply payload.

    The rich confirm-request template (order_confirmation_request_v2) sets
    each button's payload to ``<action>:<subdomain>/<order_id>``. Legacy
    ``_v1`` payloads have no prefix → default to ``confirm`` for back-compat
    with messages still in flight.
    """
    if not payload:
        return "confirm"
    head = payload.split(":", 1)[0].strip().lower()
    return head if head in _VALID_ACTIONS else "confirm"


def _phones_match(a: str | None, b: str | None) -> bool:
    """Compare two phone numbers by digits only.

    The inbound webhook gives a Cloud-API phone (digits, no ``+``); the
    order's customer phone is canonical E.164 (``+`` digits). Match on the
    full digit string, falling back to the last 9 digits (national
    significant number) to tolerate country-code formatting variance.
    """
    if not a or not b:
        return False
    da = re.sub(r"\D", "", a)
    db = re.sub(r"\D", "", b)
    if not da or not db:
        return False
    return da == db or da[-9:] == db[-9:]


async def confirm_order_from_whatsapp(
    session: AsyncSession, *, payload: str, from_phone: str
) -> bool:
    """Resolve and confirm a COD order from a quick-reply button payload.

    Defensive + idempotent: a bad payload, a non-COD order, an order not
    awaiting confirmation, a phone mismatch, or an already-confirmed order
    all short-circuit without side-effects. Returns True when the order is
    (now or already) confirmed.

    Runs on the webhook's admin (RLS-bypass) session, so it filters by the
    order's own id rather than relying on tenant context.
    """
    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    order_id = _parse_order_id(payload)
    if order_id is None:
        return False

    order_repo = OrderRepository(session)
    order = await order_repo.get_by_id(order_id)
    if order is None:
        return False

    # Idempotent replay — Meta re-delivers webhooks until they're 200'd.
    if order.customer_confirmation_status == "confirmed":
        return True

    if (order.payment_method or "").lower() != "cod":
        return False
    if order.customer_confirmation_status != "pending":
        return False
    if order.status != OrderStatus.PENDING:
        # Status moved on (cancelled / processing) — don't resurrect it.
        return False

    # Verify the tapper owns the order (canonical-phone compare).
    cust = (
        await session.execute(
            select(CustomerModel).where(CustomerModel.id == order.customer_id)
        )
    ).scalar_one_or_none()
    if cust is not None and cust.phone and not _phones_match(cust.phone, from_phone):
        logger.warning("whatsapp_confirm_phone_mismatch", order_id=str(order_id))
        return False

    try:
        order.confirm()  # PENDING → CONFIRMED (validates the transition)
    except ValueError as exc:
        logger.warning(
            "whatsapp_confirm_invalid_transition",
            order_id=str(order_id),
            error=str(exc),
        )
        return False

    order.customer_confirmation_status = "confirmed"
    order.customer_confirmed_at = datetime.now(UTC)
    updated = await order_repo.update(order)

    # Cancel any still-pending scheduled confirm sends for this order
    # (e.g. a delayed reminder that no longer needs to fire). Fail-open.
    try:
        await WhatsAppScheduledSendRepository(session).cancel_by_order(order.id)
    except Exception:
        logger.exception(
            "whatsapp_confirm_cancel_scheduled_failed", order_id=str(order_id)
        )

    await session.commit()

    # Publish OrderStatusChangedEvent so the activity log + downstream
    # side-effects fire, mirroring the gateway webhooks (paymob/kashier).
    # CONFIRMED is not in the WhatsApp handler's shipped/delivered set, so
    # this does not produce another WhatsApp message.
    try:
        from src.infrastructure.events.setup import get_event_bus

        store = await StoreRepository(session).get_by_id(order.store_id)
        event = OrderStatusChangedEvent(
            order_id=updated.id,
            order_number=updated.order_number,
            store_id=updated.store_id,
            store_name=store.name if store else "",
            customer_id=updated.customer_id,
            customer_email=str(cust.email) if cust and cust.email else None,
            customer_phone=str(cust.phone) if cust and cust.phone else None,
            customer_name=(
                f"{cust.first_name} {cust.last_name}".strip() if cust else None
            ),
            previous_status=OrderStatus.PENDING.value,
            new_status=OrderStatus.CONFIRMED.value,
            reason="confirmed_via_whatsapp",
            language=(store.default_language if store else None) or "ar",
        )
        get_event_bus().publish(event)
    except Exception:
        logger.exception(
            "whatsapp_confirm_event_publish_failed", order_id=str(order_id)
        )

    logger.info(
        "whatsapp_order_confirmed",
        order_id=str(order_id),
        store_id=str(updated.store_id),
    )
    # Acknowledge the tap with a short free-form reply (24h window is open
    # since the customer just messaged us).
    await _send_ack_reply(
        session,
        store_id=updated.store_id,
        tenant_id=updated.tenant_id,
        phone=from_phone,
        action="confirm",
        order_number=updated.order_number,
    )
    return True


# ─────────────────────────────────────────────────────────────────────
# Cancel + Postpone (rich confirm-request template, US: COD action buttons)
# ─────────────────────────────────────────────────────────────────────


async def cancel_order_from_whatsapp(
    session: AsyncSession, *, payload: str, from_phone: str
) -> bool:
    """Cancel a COD order from a "Cancel Order" quick-reply tap.

    Same defensive + idempotent contract as ``confirm_order_from_whatsapp``:
    a bad payload, non-COD order, an order not awaiting confirmation, a phone
    mismatch, or an un-cancellable status all short-circuit. Returns True when
    the order is (now or already) cancelled.
    """
    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    order_id = _parse_order_id(payload)
    if order_id is None:
        return False

    order_repo = OrderRepository(session)
    order = await order_repo.get_by_id(order_id)
    if order is None:
        return False

    # Idempotent replay.
    if order.customer_confirmation_status == "cancelled":
        return True
    if (order.payment_method or "").lower() != "cod":
        return False
    if order.customer_confirmation_status != "pending":
        return False
    if not order.can_be_cancelled():
        return False

    cust = (
        await session.execute(
            select(CustomerModel).where(CustomerModel.id == order.customer_id)
        )
    ).scalar_one_or_none()
    if cust is not None and cust.phone and not _phones_match(cust.phone, from_phone):
        logger.warning("whatsapp_cancel_phone_mismatch", order_id=str(order_id))
        return False

    try:
        order.cancel("cancelled_via_whatsapp")
    except ValueError as exc:
        logger.warning(
            "whatsapp_cancel_invalid_transition",
            order_id=str(order_id),
            error=str(exc),
        )
        return False

    order.customer_confirmation_status = "cancelled"
    from src.application.services.stock_service import try_restock_order

    await try_restock_order(session, order, reason="cancelled_via_whatsapp")
    updated = await order_repo.update(order)

    # No further confirm reminders for a cancelled order. Fail-open.
    try:
        await WhatsAppScheduledSendRepository(session).cancel_by_order(order.id)
    except Exception:
        logger.exception(
            "whatsapp_cancel_cancel_scheduled_failed", order_id=str(order_id)
        )

    await session.commit()

    try:
        from src.infrastructure.events.setup import get_event_bus

        store = await StoreRepository(session).get_by_id(order.store_id)
        event = OrderStatusChangedEvent(
            order_id=updated.id,
            order_number=updated.order_number,
            store_id=updated.store_id,
            store_name=store.name if store else "",
            customer_id=updated.customer_id,
            customer_email=str(cust.email) if cust and cust.email else None,
            customer_phone=str(cust.phone) if cust and cust.phone else None,
            customer_name=(
                f"{cust.first_name} {cust.last_name}".strip() if cust else None
            ),
            previous_status=OrderStatus.PENDING.value,
            new_status=OrderStatus.CANCELLED.value,
            reason="cancelled_via_whatsapp",
            language=(store.default_language if store else None) or "ar",
        )
        get_event_bus().publish(event)
    except Exception:
        logger.exception("whatsapp_cancel_event_publish_failed", order_id=str(order_id))

    logger.info(
        "whatsapp_order_cancelled",
        order_id=str(order_id),
        store_id=str(updated.store_id),
    )
    await _send_ack_reply(
        session,
        store_id=updated.store_id,
        tenant_id=updated.tenant_id,
        phone=from_phone,
        action="cancel",
        order_number=updated.order_number,
    )
    return True


async def postpone_order_from_whatsapp(
    session: AsyncSession, *, payload: str, from_phone: str
) -> bool:
    """Snooze a COD confirm-request from a "Postpone delivery" quick-reply tap.

    Non-destructive: the order stays PENDING. We mark
    ``customer_confirmation_status='postponed'`` and enqueue a fresh
    confirm-request scheduled send ~24h out so the customer is asked again.
    Idempotent — only the first postpone (from ``pending``) re-schedules; a
    second tap is a no-op ack so reminders don't stack.
    """
    from datetime import timedelta

    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.repositories.order_repository import OrderRepository

    order_id = _parse_order_id(payload)
    if order_id is None:
        return False

    order_repo = OrderRepository(session)
    order = await order_repo.get_by_id(order_id)
    if order is None:
        return False

    # Idempotent: only act while still awaiting the first confirmation.
    if order.customer_confirmation_status == "postponed":
        return True
    if (order.payment_method or "").lower() != "cod":
        return False
    if order.customer_confirmation_status != "pending":
        return False
    if order.status != OrderStatus.PENDING:
        return False

    cust = (
        await session.execute(
            select(CustomerModel).where(CustomerModel.id == order.customer_id)
        )
    ).scalar_one_or_none()
    if cust is not None and cust.phone and not _phones_match(cust.phone, from_phone):
        logger.warning("whatsapp_postpone_phone_mismatch", order_id=str(order_id))
        return False

    order.customer_confirmation_status = "postponed"
    await order_repo.update(order)

    # Re-ask in ~24h. Fail-open: a failed re-schedule shouldn't break the ack.
    try:
        await _reschedule_confirm_request(
            session, order=order, customer=cust, after=timedelta(hours=24)
        )
    except Exception:
        logger.exception("whatsapp_postpone_reschedule_failed", order_id=str(order_id))

    await session.commit()

    logger.info(
        "whatsapp_order_postponed",
        order_id=str(order_id),
        store_id=str(order.store_id),
    )
    await _send_ack_reply(
        session,
        store_id=order.store_id,
        tenant_id=order.tenant_id,
        phone=from_phone,
        action="postpone",
        order_number=order.order_number,
    )
    return True


async def _reschedule_confirm_request(
    session: AsyncSession, *, order, customer, after
) -> None:
    """Enqueue a fresh ``order_confirmation_request_v2`` scheduled send for an
    order, ``after`` from now, rebuilding the rich-template params from the
    order. Used by the Postpone flow.
    """
    from datetime import UTC, datetime

    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        payment_label,
    )
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    store = (
        await session.execute(select(StoreModel).where(StoreModel.id == order.store_id))
    ).scalar_one_or_none()
    if store is None or customer is None or not customer.phone:
        return

    language = (store.default_language or "ar").lower()

    # Resolve the confirm-request template row id (prefer an APPROVED one).
    tmpl_rows = (
        (
            await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.store_id == order.store_id,
                    WhatsAppTemplateModel.name == "order_confirmation_request_v2",
                )
            )
        )
        .scalars()
        .all()
    )
    template_id = None
    if tmpl_rows:
        approved = next(
            (
                t
                for t in tmpl_rows
                if getattr(t.status, "value", t.status) == "APPROVED"
            ),
            None,
        )
        template_id = (approved or tmpl_rows[0]).id

    base_loc = f"{store.subdomain}/{order.id}" if store.subdomain else str(order.id)
    total_str = f"{order.total / 100:.2f} {order.currency}"
    _addr = order.shipping_address or {}
    address_str = (
        ", ".join(
            str(p).strip()
            for p in (
                _addr.get("address_line1"),
                _addr.get("address_line2"),
                _addr.get("city"),
            )
            if p and str(p).strip()
        )
        or "-"
    )
    customer_name = f"{customer.first_name} {customer.last_name}".strip()

    await WhatsAppScheduledSendRepository(session).create(
        tenant_id=order.tenant_id,
        store_id=order.store_id,
        phone=customer.phone,
        scheduled_for=datetime.now(UTC) + after,
        template_id=template_id,
        template_params={
            "customer_name": customer_name,
            "store_name": store.name,
            "order_number": order.order_number,
            "total": total_str,
            "payment_label": payment_label(order.payment_method, language),
            "item_count": str(len(order.line_items or [])),
            "address": address_str,
            "confirm_payload": base_loc,
        },
        customer_id=order.customer_id,
        related_order_id=order.id,
    )


# Localized acknowledgement replies for each action. en/ar only.
_ACK_TEXT: dict[str, dict[str, str]] = {
    "confirm": {
        "en": "✅ Thanks! Order {n} is confirmed — we'll start preparing it.",
        "ar": "✅ شكراً! تم تأكيد الأوردر {n} وهنبدأ التحضير.",
    },
    "cancel": {
        "en": "❌ Order {n} has been cancelled. Let us know if this was a mistake.",
        "ar": "❌ تم إلغاء الأوردر {n}. لو ده كان بالغلط كلمنا.",
    },
    "postpone": {
        "en": "⏰ No problem — we'll check back with you about order {n} tomorrow.",
        "ar": "⏰ ولا يهمك — هنرجعلك بخصوص الأوردر {n} بكرة.",
    },
}


async def _send_ack_reply(
    session: AsyncSession,
    *,
    store_id: UUID,
    tenant_id,
    phone: str,
    action: str,
    order_number: str,
) -> None:
    """Send a short free-form acknowledgement of a button tap. Best-effort:
    runs inside the open 24h customer-service window; failures are swallowed.
    """
    try:
        from src.infrastructure.database.models.tenant.store import StoreModel
        from src.infrastructure.external_services.whatsapp import get_whatsapp_service

        store = (
            await session.execute(select(StoreModel).where(StoreModel.id == store_id))
        ).scalar_one_or_none()
        lang = "ar"
        if store is not None and (store.default_language or "ar").lower().startswith(
            "en"
        ):
            lang = "en"
        template = _ACK_TEXT.get(action, {}).get(lang)
        if not template:
            return
        text = template.format(n=order_number)
        service = await get_whatsapp_service(store_id, session, tenant_id)
        await service.send_text_message(phone, text)
    except Exception:
        logger.exception("whatsapp_ack_reply_failed", store_id=str(store_id))

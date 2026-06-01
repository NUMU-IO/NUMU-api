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

from src.config.logging_config import get_logger
from src.core.entities.order import OrderStatus
from src.core.events.order_events import OrderStatusChangedEvent

logger = get_logger(__name__)


def _parse_order_id(payload: str) -> UUID | None:
    """Extract the order UUID from a ``<subdomain>/<order_id>`` (or bare
    ``<order_id>``) quick-reply payload. Returns None when unparseable."""
    if not payload:
        return None
    candidate = payload.rsplit("/", 1)[-1].strip()
    try:
        return UUID(candidate)
    except ValueError:
        return None


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
    return True

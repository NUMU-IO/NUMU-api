"""Is this store allowed to send on WhatsApp right now, and how much is left.

The channel costs NUMU money on every template message, so access is sold
rather than granted: an admin prices a store's request, the merchant pays and
uploads the receipt through the existing InstaPay subscription-proof flow, and
verification switches the channel on until ``active_until``.

One question, one answer, one place — every send path and every settings screen
reads ``entitlement()`` rather than re-deriving "approved" from a status column
that no longer tells the whole story.

Backwards compatible on purpose: a row that is APPROVED with no ``active_until``
is a free grant from before this existed, and stays live. Only rows that were
actually sold carry an expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.wallet_settings import get_wallet_settings
from src.core.entities.subscription_payment import (
    SubscriptionPaymentIntentStatus,
    SubscriptionPaymentPurpose,
)
from src.core.logging import get_logger
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
)
from src.infrastructure.database.models.public.whatsapp_access import (
    WhatsAppAccessRequestModel,
    WhatsAppAccessStatus,
)
from src.infrastructure.database.models.tenant.message_log import MessageLogModel
from src.infrastructure.external_services.instapay.payment_service import (
    generate_reference_code,
)
from src.infrastructure.external_services.instapay.qr_generator import (
    build_qr_payload,
)

logger = get_logger(__name__)

#: Billing cycle → how long one paid period runs.
CYCLE_DAYS = {"monthly": 30, "quarterly": 90, "yearly": 365}

#: How long a WhatsApp bill stays payable. A merchant paying at the bank is not
#: on the 60-minute clock of a self-serve plan checkout.
PAYMENT_WINDOW = timedelta(days=14)


class BillingUnavailableError(RuntimeError):
    """NUMU has no InstaPay address configured, so nothing can be billed."""


@dataclass(frozen=True)
class Entitlement:
    """What the store may do on WhatsApp, and why."""

    #: The one flag every send path cares about.
    active: bool
    status: str
    #: None when the grant has no expiry (legacy free grants, admin grants).
    active_until: datetime | None = None
    #: None = uncapped.
    allowance: int | None = None
    used: int = 0
    #: Why sending is off, for the UI: not_requested | pending | awaiting_payment
    #: | rejected | disabled | expired | allowance_exhausted | None.
    reason: str | None = None

    @property
    def remaining(self) -> int | None:
        if self.allowance is None:
            return None
        return max(0, self.allowance - self.used)


def period_start(row: WhatsAppAccessRequestModel, now: datetime) -> datetime:
    """Start of the period usage is counted against.

    Anchored to the paid period's end, not to the calendar month: a merchant
    who paid on the 9th gets their allowance back on the 9th, which is the
    date they will recognise from their receipt.
    """
    if row.active_until is None:
        return now - timedelta(days=30)
    days = CYCLE_DAYS.get(row.billing_cycle or "monthly", 30)
    return row.active_until - timedelta(days=days)


async def count_messages(db: AsyncSession, store_id: UUID, since: datetime) -> int:
    """Outbound template messages billed to this store since ``since``.

    Counts template sends only: free-form replies inside the 24-hour service
    window cost nothing, so charging a merchant's allowance for them would be
    charging for something NUMU never paid for.
    """
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MessageLogModel)
            .where(
                MessageLogModel.store_id == store_id,
                MessageLogModel.template_name.isnot(None),
                MessageLogModel.created_at >= since,
            )
        )
        or 0
    )


async def entitlement(
    db: AsyncSession, store_id: UUID, *, now: datetime | None = None
) -> Entitlement:
    """The store's live WhatsApp entitlement."""
    now = now or datetime.now(UTC)
    row = (
        await db.execute(
            select(WhatsAppAccessRequestModel).where(
                WhatsAppAccessRequestModel.store_id == store_id
            )
        )
    ).scalar_one_or_none()

    if row is None:
        return Entitlement(active=False, status="none", reason="not_requested")

    status = row.status.value
    if row.status != WhatsAppAccessStatus.APPROVED:
        return Entitlement(
            active=False,
            status=status,
            active_until=row.active_until,
            allowance=row.message_allowance,
            reason=status,
        )

    # APPROVED but the paid period has run out — the expiry sweep will move the
    # row, but a send must not wait for the sweep to catch up.
    if row.active_until is not None and row.active_until <= now:
        return Entitlement(
            active=False,
            status=status,
            active_until=row.active_until,
            allowance=row.message_allowance,
            reason="expired",
        )

    used = await count_messages(db, store_id, period_start(row, now))
    if row.message_allowance is not None and used >= row.message_allowance:
        return Entitlement(
            active=False,
            status=status,
            active_until=row.active_until,
            allowance=row.message_allowance,
            used=used,
            reason="allowance_exhausted",
        )

    return Entitlement(
        active=True,
        status=status,
        active_until=row.active_until,
        allowance=row.message_allowance,
        used=used,
    )


async def activate_paid_access(
    db: AsyncSession,
    *,
    store_id: UUID,
    billing_cycle: str = "monthly",
    allowance: int | None = None,
    intent_id: UUID | None = None,
    now: datetime | None = None,
) -> WhatsAppAccessRequestModel | None:
    """Switch the channel on for one paid period. Called on payment verification.

    Extends rather than replaces: a merchant who pays early keeps the days they
    have left, so paying on time is never punished.
    """
    now = now or datetime.now(UTC)
    row = (
        await db.execute(
            select(WhatsAppAccessRequestModel).where(
                WhatsAppAccessRequestModel.store_id == store_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        logger.warning("whatsapp_activate_no_access_row", store_id=str(store_id))
        return None

    days = CYCLE_DAYS.get(billing_cycle, 30)
    base = (
        row.active_until
        if row.active_until is not None and row.active_until > now
        else now
    )
    row.active_until = base + timedelta(days=days)
    row.status = WhatsAppAccessStatus.APPROVED
    row.billing_cycle = billing_cycle
    if allowance is not None:
        row.message_allowance = allowance
    if intent_id is not None:
        row.payment_intent_id = intent_id
    row.reviewed_at = now

    logger.info(
        "whatsapp_paid_access_activated",
        store_id=str(store_id),
        active_until=row.active_until.isoformat(),
        allowance=row.message_allowance,
    )
    return row


async def expire_lapsed_access(
    db: AsyncSession, *, now: datetime | None = None
) -> dict:
    """Flip every APPROVED row whose paid period has ended to EXPIRED."""
    now = now or datetime.now(UTC)
    rows = (
        (
            await db.execute(
                select(WhatsAppAccessRequestModel).where(
                    WhatsAppAccessRequestModel.status == WhatsAppAccessStatus.APPROVED,
                    WhatsAppAccessRequestModel.active_until.isnot(None),
                    WhatsAppAccessRequestModel.active_until <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = WhatsAppAccessStatus.EXPIRED
        logger.info("whatsapp_access_expired", store_id=str(row.store_id))
    return {"expired": len(rows)}


async def open_payment(
    db: AsyncSession,
    row: WhatsAppAccessRequestModel,
    *,
    created_by_user_id: UUID | None,
    now: datetime | None = None,
) -> SubscriptionPaymentIntentModel:
    """The payment the merchant settles this period's WhatsApp bill against.

    Returns the bill already open at the same price rather than minting a new
    reference per click: a merchant who wrote one code in a transfer note must
    find that same code when they come back to upload the receipt.
    """
    now = now or datetime.now(UTC)
    if row.payment_intent_id is not None:
        current = await db.get(SubscriptionPaymentIntentModel, row.payment_intent_id)
        if (
            current is not None
            and current.amount_cents == row.amount_cents
            and current.billing_cycle == (row.billing_cycle or "monthly")
            and current.status
            in (
                SubscriptionPaymentIntentStatus.AWAITING_PROOF.value,
                SubscriptionPaymentIntentStatus.UNDER_REVIEW.value,
            )
            and (current.expires_at is None or current.expires_at > now)
        ):
            return current

    destination = (await get_wallet_settings(db)).instapay_ipa
    if not destination:
        raise BillingUnavailableError("instapay_not_configured")

    reference = generate_reference_code(prefix="SUB")
    for _ in range(3):
        taken = await db.scalar(
            select(SubscriptionPaymentIntentModel.id).where(
                SubscriptionPaymentIntentModel.special_reference == reference
            )
        )
        if taken is None:
            break
        reference = generate_reference_code(prefix="SUB")

    intent = SubscriptionPaymentIntentModel(
        tenant_id=row.tenant_id,
        created_by_user_id=created_by_user_id,
        plan_key=row.plan_key or "whatsapp",
        billing_cycle=row.billing_cycle or "monthly",
        purpose=SubscriptionPaymentPurpose.WHATSAPP_ADDON.value,
        amount_cents=row.amount_cents,
        currency=row.currency or "EGP",
        status=SubscriptionPaymentIntentStatus.AWAITING_PROOF.value,
        special_reference=reference,
        display_destination=destination,
        qr_payload=build_qr_payload(
            ipa=destination,
            amount_cents=row.amount_cents,
            reference_code=reference,
            note=f"NUMU WhatsApp {reference}",
        ),
        expires_at=now + PAYMENT_WINDOW,
    )
    db.add(intent)
    await db.flush()
    row.payment_intent_id = intent.id
    return intent

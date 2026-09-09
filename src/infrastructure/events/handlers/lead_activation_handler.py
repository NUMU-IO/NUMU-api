"""Stamp the activation milestone on a merchant lead when its first order is paid.

``merchant_leads`` tracks a merchant from first touch to activation, and
``LEAD_STATUS_ORDER`` ends at ``activated``. Nothing ever wrote it. The
column and the status existed, the admin funnel read them, and the last
column was structurally always zero — which is worse than not having the
metric, because a funnel that reports 0% activation looks like a product
problem rather than a missing write.

Activation is defined as the merchant's first *paid* order, so this
subscribes to ``OrderPaidEvent`` — the point every payment path converges
on (gateway webhooks, InstaPay proof approval, COD delivery confirmation).
"Paid" rather than "placed" is the bar on purpose: an order that is
created and never paid tells us the storefront works, not that the
merchant has a business.

Deliberately *not* filtered for the merchant's own test orders. Guessing
at that here would mean either a heuristic that silently drops real first
orders, or a definition that drifts from the one analytics already uses.
A merchant who paid for their own test order has still taken a payment
end-to-end, which is most of what this metric is for.

Idempotency: ``first_order_at`` is fill-only and ``advance_status`` never
moves backwards, so redelivery of the same event is a no-op. Like every
other handler on this bus it runs post-commit in its own session and
never raises — a lead-tracking failure must not affect the order.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from src.application.services import referral_service
from src.core.events.order_events import OrderPaidEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal

logger = get_logger(__name__)


async def handle_lead_activation_on_order_paid(event: OrderPaidEvent) -> None:
    """Mark the lead behind this store as activated. Never raises."""
    log = logger.bind(
        order_id=str(event.order_id),
        order_number=event.order_number,
        store_id=str(event.store_id),
    )

    from src.infrastructure.database.models.public.merchant_lead import (
        MerchantLeadModel,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                store = await StoreRepository(session).get_by_id(event.store_id)
                if store is None:
                    log.warning("lead_activation_store_not_found")
                    return

                # Leads are linked by tenant, not store: a tenant with two
                # stores is still one merchant, and the first paid order on
                # either one is the moment they activated.
                lead = (
                    await session.execute(
                        select(MerchantLeadModel)
                        .where(MerchantLeadModel.tenant_id == store.tenant_id)
                        .limit(1)
                    )
                ).scalar_one_or_none()

                if lead is None:
                    # Every merchant who signed up after the leads table
                    # shipped has a row. Older tenants do not, and that is
                    # expected — backfilling them is a separate job.
                    log.info("lead_activation_no_lead_for_tenant")
                    return

                already = lead.first_order_at is not None
                if lead.first_order_at is None:
                    lead.first_order_at = datetime.now(UTC)
                lead.advance_status("activated")
                lead.last_seen_at = datetime.now(UTC)

                # The milestone the referral programme is built around: a
                # referrer earns when the merchant they brought actually
                # sells, not when they sign up.
                await session.flush()
                await referral_service.accrue_for_lead(session, lead.id)

            if not already:
                log.insight(
                    "merchant_lead_activated",
                    lead_id=str(lead.id),
                    tenant_id=str(store.tenant_id),
                )
    except Exception:
        log.exception("lead_activation_failed")

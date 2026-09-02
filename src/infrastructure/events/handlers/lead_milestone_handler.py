"""Stamp the remaining merchant-lead milestones: first product, first commission.

``first_order_at`` got its writer with the activation handler. These are
the two either side of it — the merchant built something, and the
merchant started paying us — and together the three answer the only
questions that matter about a merchant's first weeks: did they set up a
store, did they sell, did we make money.

``first_product_at`` fires on ``ProductCreatedEvent``. It is not the same
as the onboarding step being ticked: a merchant can complete onboarding
without a real catalogue, and the step is a boolean with no date on it.

``first_commission_at`` is stamped by the wallet commission handler
rather than here, because there is no "commission charged" event to
subscribe to and inventing one to carry a timestamp would be a worse
trade than exporting one function.

Both are fill-only, so redelivery cannot move a date, and both run
post-commit in their own session and never raise — a lead-tracking
failure must not affect the product or the charge that triggered it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.events.product_events import ProductCreatedEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal

logger = get_logger(__name__)


async def stamp_lead_milestone(
    session: AsyncSession, *, tenant_id: UUID, field: str
) -> bool:
    """Fill *field* on the lead for *tenant_id* if it is still empty.

    Returns True when this call was the one that set it. Runs in the
    caller's session and inside a savepoint, so a failure here rolls back
    only itself — callers include the commission handler, where poisoning
    the outer transaction would undo a charge the merchant already owes.
    """
    from src.infrastructure.database.models.public.merchant_lead import (
        MerchantLeadModel,
    )

    async with session.begin_nested():
        lead = (
            (
                await session.execute(
                    select(MerchantLeadModel).where(
                        MerchantLeadModel.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if lead is None or getattr(lead, field) is not None:
            return False
        setattr(lead, field, datetime.now(UTC))
        lead.last_seen_at = datetime.now(UTC)
        return True


async def handle_lead_first_product(event: ProductCreatedEvent) -> None:
    """Stamp ``first_product_at`` for the store's tenant. Never raises."""
    log = logger.bind(product_id=str(event.product_id), store_id=str(event.store_id))

    from src.infrastructure.repositories.store_repository import StoreRepository

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                store = await StoreRepository(session).get_by_id(event.store_id)
                if store is None:
                    log.warning("lead_first_product_store_not_found")
                    return
                stamped = await stamp_lead_milestone(
                    session, tenant_id=store.tenant_id, field="first_product_at"
                )
        if stamped:
            log.insight("merchant_lead_first_product", tenant_id=str(store.tenant_id))
    except Exception:
        log.exception("lead_first_product_failed")

"""Mark an abandoned checkout recovered when the shopper finally orders.

``abandoned_checkouts`` has carried ``recovered_at`` and ``recovered_order_id``
since it was created, and the entity docstring says a completed payment
"graduates the row into an Order and sets ``recovered_at``" — but nothing ever
set them. Every row therefore stayed abandoned forever, with three consequences:

* the merchant's abandoned list kept showing carts the customer had already
  bought, and the nudge buttons stayed live on them;
* recovery rate was structurally zero, so the feature could never show it was
  working;
* the recovery LINK kept restoring a cart that had already been converted, so
  reopening it resurrected items the shopper had just paid for.

Subscribed to ``OrderCreatedEvent`` rather than threading a checkout id through
the whole checkout flow: an order can be created from the storefront, the
merchant hub, a payment webhook or a draft conversion, and only the event is
common to all of them.

## Matching

There is no foreign key from an order back to the checkout it came from, so
attribution is by contact detail: the store's most recent un-recovered checkout
whose phone or email matches the order's customer. Phones are compared in
E.164 (``PhoneNumber``'s normaliser, the one the reminders key on), because the
two records are written by different paths and one may carry ``01…`` where the
other carries ``+201…``.

Deliberately conservative. A missed attribution understates the recovery rate;
a wrong one tells a merchant a cart was recovered when it wasn't, and hides a
still-recoverable cart from their list. So: exact contact match, same store,
newest first, one row only.
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select

from src.core.events.order_events import OrderCreatedEvent
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.database.connection import AsyncSessionLocal

logger = structlog.get_logger(__name__)

__all__ = ["handle_order_created_recovery"]


async def handle_order_created_recovery(event: OrderCreatedEvent) -> None:
    """Attribute this order to an abandoned checkout, if one matches."""
    from src.infrastructure.database.models.tenant.abandoned_checkout import (
        AbandonedCheckoutModel,
    )
    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.database.models.tenant.order import OrderModel

    try:
        async with AsyncSessionLocal() as db:
            order = (
                await db.execute(
                    select(OrderModel).where(OrderModel.id == event.order_id)
                )
            ).scalar_one_or_none()
            if order is None:
                return

            customer = (
                await db.execute(
                    select(CustomerModel).where(CustomerModel.id == event.customer_id)
                )
            ).scalar_one_or_none()

            phone = getattr(customer, "phone", None)
            email = getattr(customer, "email", None)
            if not phone and not email:
                return

            # Candidates: this store's un-recovered checkouts for this contact.
            # A row keeps the phone as typed (01…, +20 10…), so SQL only narrows
            # on its last digits; the match itself is made in E.164 below.
            conditions = []
            if email:
                conditions.append(AbandonedCheckoutModel.email == email)
            if phone:
                phone = PhoneNumber(value=phone).value
                conditions.append(AbandonedCheckoutModel.phone.like(f"%{phone[-4:]}"))
            if not conditions:
                return

            rows = (
                (
                    await db.execute(
                        select(AbandonedCheckoutModel)
                        .where(
                            AbandonedCheckoutModel.store_id == event.store_id,
                            AbandonedCheckoutModel.recovered_at.is_(None),
                            or_(*conditions),
                        )
                        .order_by(AbandonedCheckoutModel.last_activity_at.desc())
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )

            target = None
            for row in rows:
                if email and row.email and row.email == email:
                    target = row
                    break
                if phone and row.phone and PhoneNumber(value=row.phone).value == phone:
                    target = row
                    break
            if target is None:
                return

            target.recovered_at = datetime.now(UTC)
            target.recovered_order_id = event.order_id
            await db.commit()

            logger.info(
                "abandoned_checkout_recovered",
                checkout_id=str(target.id),
                order_id=str(event.order_id),
                store_id=str(event.store_id),
            )
    except Exception:
        # Attribution is reporting, not correctness — it must never break an
        # order that has already been paid for.
        logger.warning("abandoned_recovery_attribution_failed", exc_info=True)

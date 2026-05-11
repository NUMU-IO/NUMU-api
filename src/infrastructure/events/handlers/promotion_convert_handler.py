"""Fire `convert` PromotionEvent rows when an order with applied
promotions completes.

Hooked into the existing `OrderPaidEvent` pathway (which fires both
on Paymob webhook and on COD merchant-mark-paid), so every payment
flow records conversions consistently — no per-flow changes needed.

Resolution rules:

* Look up the order's `metadata.coupon_code`. If present, find the
  active `discount_code` promotion linked to that coupon and emit one
  `convert` event for it.
* Look up every active automatic promotion eligible for the order's
  store (we don't track which auto-discounts actually applied to a
  specific order in v1 — the resolver picks them at cart-recompute
  time and they aren't persisted on the order). Emit a `convert` for
  each. The merchant analytics then over-counts auto-discount
  conversions slightly when multiple were eligible — acceptable for
  v1 and tracked as a known limitation.

Idempotent: the event log allows duplicates (it's append-only), but
we check whether a `convert` event for `(order_id, promotion_id)`
already exists before writing. Re-firing OrderPaidEvent (which can
happen on Paymob webhook retries) won't double-count.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select, text

from src.core.entities.promotion_event import PromotionEvent
from src.core.events.order_events import OrderPaidEvent
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models import (
    OrderModel,
    PromotionEventModel,
    PromotionModel,
)
from src.infrastructure.repositories.coupon_repository import CouponRepository
from src.infrastructure.repositories.promotion_event_repository import (
    PromotionEventRepository,
)
from src.infrastructure.repositories.promotion_repository import (
    PromotionRepository,
)

log = logging.getLogger(__name__)


async def handle_promotion_convert_on_order_paid(event: OrderPaidEvent) -> None:
    """Emit convert events for every promotion attributable to this order."""
    try:
        async with AsyncSessionLocal() as session:
            # Read the full row set without RLS — order/promotion/coupon
            # belong to the same tenant by construction, but the event
            # arrives without request-level tenant context.
            await session.execute(
                text("SELECT set_config('app.rls_bypass','true',true)")
            )

            order = (
                await session.execute(
                    select(OrderModel).where(OrderModel.id == event.order_id)
                )
            ).scalar_one_or_none()
            if order is None:
                log.warning("convert_handler: order not found id=%s", event.order_id)
                return

            tenant_id = order.tenant_id
            store_id = order.store_id
            # OrderModel.coupon_code is the canonical column; older code
            # paths used to stuff it into `extra_data["coupon_code"]`
            # too — read both for safety.
            coupon_code = order.coupon_code or (
                (order.extra_data or {}).get("coupon_code")
            )

            event_repo = PromotionEventRepository(session)
            promo_repo = PromotionRepository(session)
            coupon_repo = CouponRepository(session)

            # Skip rows we've already fired a convert for — Paymob can
            # replay webhooks; merchant COD-mark-paid can also fire twice
            # if the merchant clicks twice.
            already = (
                (
                    await session.execute(
                        select(PromotionEventModel.promotion_id).where(
                            PromotionEventModel.order_id == event.order_id,
                            PromotionEventModel.event_type == "convert",
                        )
                    )
                )
                .scalars()
                .all()
            )
            already_set = set(already)

            now = datetime.now(UTC)
            convert_events: list[PromotionEvent] = []

            # 1) Resolve any code-based promotion linked to the order's
            #    coupon code.
            if coupon_code:
                coupon = await coupon_repo.get_by_code(store_id, coupon_code)
                if coupon is not None:
                    # Find an active promotion that wraps this coupon.
                    linked_promo = (
                        await session.execute(
                            select(PromotionModel).where(
                                PromotionModel.coupon_id == coupon.id,
                                PromotionModel.status == "active",
                            )
                        )
                    ).scalar_one_or_none()
                    if linked_promo is not None and linked_promo.id not in already_set:
                        convert_events.append(
                            PromotionEvent.convert(
                                tenant_id=tenant_id,
                                store_id=store_id,
                                promotion_id=linked_promo.id,
                                order_id=event.order_id,
                                customer_id=event.customer_id,
                                metadata={
                                    "source": "code",
                                    "code": coupon_code,
                                    "order_total": float(event.total),
                                },
                            )
                        )

            # 2) Auto-discounts: any active `automatic` promo for this
            #    store at the moment of order completion. v1 over-counts
            #    when multiple are eligible — acceptable per the spec
            #    (§4 attribution rules).
            auto_promos = await promo_repo.list_active_for_storefront(store_id, now)
            for promo in auto_promos:
                if promo.surface.value != "automatic":
                    continue
                if promo.id in already_set:
                    continue
                convert_events.append(
                    PromotionEvent.convert(
                        tenant_id=tenant_id,
                        store_id=store_id,
                        promotion_id=promo.id,
                        order_id=event.order_id,
                        customer_id=event.customer_id,
                        metadata={
                            "source": "automatic",
                            "order_total": float(event.total),
                        },
                    )
                )

            if not convert_events:
                return

            await event_repo.record_many(convert_events)
            await session.commit()
            log.info(
                "promotion_convert_recorded order_id=%s count=%d",
                event.order_id,
                len(convert_events),
            )
    except Exception:  # noqa: BLE001 — never block payment flow on analytics
        log.exception("promotion_convert_handler failed for order=%s", event.order_id)

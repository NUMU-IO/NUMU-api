"""COD-to-prepaid recovery offer for the native storefront — the "recover" flow.

When a high-risk COD order is ALLOWED under ``cod_trust.action == "recover"``
(see ``cod_trust_service``), we let the order through but schedule a WhatsApp
message offering the buyer a payment link — with an optional merchant promo —
to pay online instead. If they pay, the order converts to prepaid; if not, it
proceeds as COD (or the merchant's auto-RTO sweep handles it later).

This reuses the proven ``order_confirmation`` scheduled-send pattern: resolve
the merchant's approved template, build the params, and enqueue a
``WhatsAppScheduledSend`` row that the existing dispatcher beat-task sends. The
``cod_recovery_offer_v1`` template is a Meta-approved asset the merchant
provisions; until it exists this is a graceful no-op (logged), so the code is
safe to ship ahead of template approval.

Best-effort throughout: a recovery-offer failure must NEVER affect the order
that was already created.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)

# Send the offer a short while after the order so it doesn't race the
# order-confirmation message that fires immediately on creation.
DEFAULT_OFFER_DELAY = timedelta(minutes=10)
RECOVERY_TEMPLATE_NAME = "cod_recovery_offer_v1"


async def schedule_cod_recovery_offer(
    session: Any,
    *,
    order: Any,
    store: Any,
    customer: Any,
) -> bool:
    """Schedule the WhatsApp pay-online offer for a recover-flagged COD order.

    Returns ``True`` if a send was scheduled, ``False`` if skipped (no phone,
    no approved template, etc.). Never raises.
    """
    try:
        from sqlalchemy import select

        from src.infrastructure.database.models.tenant.whatsapp_template import (
            WhatsAppTemplateModel,
        )
        from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
            WhatsAppScheduledSendRepository,
        )

        phone = getattr(customer, "phone", None) if customer is not None else None
        if not phone:
            logger.info(
                "cod_recovery_offer_skipped_no_phone",
                extra={"order_id": str(getattr(order, "id", None))},
            )
            return False

        # Resolve the recovery template (prefer an APPROVED row).
        rows = (
            (
                await session.execute(
                    select(WhatsAppTemplateModel).where(
                        WhatsAppTemplateModel.store_id == order.store_id,
                        WhatsAppTemplateModel.name == RECOVERY_TEMPLATE_NAME,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            logger.info(
                "cod_recovery_offer_skipped_no_template",
                extra={"store_id": str(order.store_id)},
            )
            return False
        approved = next(
            (t for t in rows if getattr(t.status, "value", t.status) == "APPROVED"),
            None,
        )
        template_id = (approved or rows[0]).id

        # Optional merchant promo shown in the offer (e.g. "10% off if you pay
        # online"). Lives alongside the other cod_trust settings. Meta rejects
        # blank template variables, so fall back to a localized default line
        # when the merchant set no promo.
        cod_trust = (getattr(store, "settings", None) or {}).get("cod_trust") or {}
        language = (getattr(store, "default_language", "ar") or "ar").lower()
        default_promo = (
            "ادفع أونلاين لتأكيد طلبك."
            if language.startswith("ar")
            else "Pay online to secure your order."
        )
        promo = str(cod_trust.get("recovery_promo") or "").strip() or default_promo

        subdomain = getattr(store, "subdomain", None)
        base_loc = f"{subdomain}/{order.id}" if subdomain else str(order.id)
        total_str = f"{order.total / 100:.2f} {order.currency}"
        customer_name = (
            f"{getattr(customer, 'first_name', '') or ''} "
            f"{getattr(customer, 'last_name', '') or ''}"
        ).strip()

        await WhatsAppScheduledSendRepository(session).create(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            phone=phone,
            scheduled_for=datetime.now(UTC) + DEFAULT_OFFER_DELAY,
            template_id=template_id,
            template_params={
                "customer_name": customer_name,
                "store_name": getattr(store, "name", ""),
                "order_number": order.order_number,
                "total": total_str,
                "promo": promo,
                # The storefront resolves this to the order's pay page.
                "pay_payload": base_loc,
            },
            customer_id=order.customer_id,
            related_order_id=order.id,
        )
        logger.info(
            "cod_recovery_offer_scheduled",
            extra={"order_id": str(order.id), "store_id": str(order.store_id)},
        )
        return True
    except Exception as exc:  # noqa: BLE001 — never break the created order
        # Keyword fields, NOT printf args: `get_logger` returns `Log`, whose
        # `warning(self, event: str, **kwargs)` accepts no positional extras
        # (src/core/logging.py). The old `logger.warning("...: %s", exc)` made
        # this handler raise `TypeError` instead of swallowing — so the one
        # function documented as "never raises" raised, and because
        # `schedule_cod_recovery_offer` is awaited unguarded in the checkout
        # flow AFTER the order row is committed, any hiccup here turned a
        # successful order into a 500 for the shopper (and an invitation to
        # order again). Introduced when logging moved to the keyword-only
        # `Log`; the printf call site was not migrated with it.
        logger.warning(
            "cod_recovery_offer_failed",
            extra={"order_id": str(order.id), "error": str(exc)},
        )
        return False

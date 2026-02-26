"""Fawry webhook processing service.

Orchestrates the work of multiple domain agents:
- DB Agent: Order lookup and status updates via OrderModel
- Payment Agent: Payment state transitions with guard checks
- Inventory Agent: Releases reserved stock on expiry via ProductModel
- Messaging Agent: WhatsApp notifications via WhatsAppMessagingService
- Security Agent: Replay protection via Redis nonce + timestamp checks
- Audit Agent: Logs events via AuditLogModel
"""

import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.interfaces.services.messaging_service import (
    MessageContent,
    MessageRecipient,
    MessageType,
)
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)
from src.infrastructure.tenancy.rls import narrow_to_tenant

logger = get_logger(__name__)

# Replay protection constants
NONCE_TTL_SECONDS = 86400  # 24 hours (full day)
MAX_WEBHOOK_AGE_SECONDS = 15 * 60  # 15 minutes

# Valid prior states for each webhook status. Webhooks arriving when the
# order is already in a later state are silently ignored (idempotency).
_PAID_VALID_PRIOR = {OrderStatus.PENDING, OrderStatus.PAYMENT_FAILED}
_EXPIRED_VALID_PRIOR = {OrderStatus.PENDING}
_CANCELED_VALID_PRIOR = {
    OrderStatus.PENDING,
    OrderStatus.CONFIRMED,
    OrderStatus.PROCESSING,
}


class FawryWebhookService:
    """Processes Fawry webhook callbacks with full DB wiring.

    Coordinates Security, DB, Payment, Inventory, Messaging, and Audit
    agents for each webhook status.
    """

    def __init__(
        self,
        db: AsyncSession,
        cache: RedisCacheService | None = None,
        messaging: WhatsAppMessagingService | None = None,
    ) -> None:
        self.db = db
        self.cache = cache
        self.messaging = messaging

    # ------------------------------------------------------------------ #
    # Security Agent: replay protection
    # ------------------------------------------------------------------ #

    async def check_replay(self, reference_number: str) -> bool:
        """Return True if this webhook is a duplicate (replay).

        Uses Redis SETNX to atomically check-and-set a processed key.
        """
        if not self.cache:
            return False

        nonce_key = f"fawry:processed:{reference_number}"
        was_set = await self.cache.set_if_absent(
            nonce_key, "1", expire=NONCE_TTL_SECONDS
        )
        return not was_set  # True = duplicate

    def check_timestamp(self, data: dict) -> bool:
        """Return True if the webhook timestamp is within the allowed window.

        Checks the ``timestamp`` field (epoch ms) which represents when
        Fawry dispatched the webhook.  ``orderExpiryDate`` is the payment
        reference expiry and is NOT used — it points to the future and
        would always pass.

        If no usable timestamp is present we accept the webhook (Fawry
        sandbox does not always include timestamps).
        """
        timestamp_ms = data.get("timestamp")
        if not timestamp_ms:
            return True  # No timestamp in payload — accept

        try:
            webhook_time = int(timestamp_ms) / 1000  # Convert ms → seconds
            age = time.time() - webhook_time
            return abs(age) <= MAX_WEBHOOK_AGE_SECONDS
        except (ValueError, TypeError):
            return True  # Unparseable timestamp — accept

    # ------------------------------------------------------------------ #
    # DB Agent: order lookup (tenant-safe)
    # ------------------------------------------------------------------ #

    async def _get_order_by_payment_id(self, payment_id: str) -> OrderModel | None:
        """Fetch the order row by payment_id with a row-level lock.

        Uses ``SELECT ... FOR UPDATE`` so that concurrent webhook
        deliveries for the same order block until the first transaction
        commits, preventing race-condition state corruption.
        """
        result = await self.db.execute(
            select(OrderModel)
            .where(OrderModel.payment_id == payment_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # Audit Agent: logging
    # ------------------------------------------------------------------ #

    async def _create_audit_log(
        self,
        event_type: str,
        order: OrderModel,
        details: dict,
        severity: str = "info",
    ) -> None:
        """Insert an audit log entry for the webhook event."""
        audit = AuditLogModel(
            event_type=event_type,
            severity=severity,
            store_id=order.store_id,
            tenant_id=order.tenant_id,
            resource_type="order",
            resource_id=str(order.id),
            action="update",
            details=details,
        )
        self.db.add(audit)

    # ------------------------------------------------------------------ #
    # Inventory Agent: release reserved stock (tenant-scoped)
    # ------------------------------------------------------------------ #

    async def _release_inventory(self, order: OrderModel) -> None:
        """Restore product quantities from order line items.

        The UPDATE is scoped to the order's tenant_id as a defense-in-depth
        measure alongside RLS policies.
        """
        line_items: list[dict] = order.line_items or []
        for item in line_items:
            product_id = item.get("product_id")
            qty = item.get("quantity", 0)
            if product_id and qty > 0:
                await self.db.execute(
                    update(ProductModel)
                    .where(
                        ProductModel.id == UUID(product_id),
                        ProductModel.tenant_id == order.tenant_id,
                    )
                    .values(quantity=ProductModel.quantity + qty)
                )
        logger.info(
            "inventory_released",
            order_number=order.order_number,
            line_item_count=len(line_items),
        )

    # ------------------------------------------------------------------ #
    # Messaging Agent: WhatsApp notifications
    # ------------------------------------------------------------------ #

    async def _notify_merchant_cancelled(self, order: OrderModel) -> None:
        """Send a WhatsApp notification to the merchant about cancellation."""
        if not self.messaging:
            logger.debug("whatsapp_skipped", reason="messaging not configured")
            return

        store = order.store
        if not store or not store.contact_phone:
            logger.warning(
                "whatsapp_skipped",
                reason="no merchant phone",
                order_number=order.order_number,
            )
            return

        recipient = MessageRecipient(
            phone=store.contact_phone,
            name=store.name,
            language=getattr(store, "default_language", "en") or "en",
        )

        content = MessageContent(
            type=MessageType.ORDER_CANCELLED,
            recipient=recipient,
            template_params={
                "customer_name": store.name,
                "order_number": order.order_number,
                "total": f"{order.currency} {order.total / 100:.2f}",
                "store_name": store.name,
            },
        )

        try:
            result = await self.messaging.send_message(content)
            if not result.success:
                logger.warning(
                    "whatsapp_send_failed",
                    order_number=order.order_number,
                    error=result.error_message,
                )
        except Exception as e:
            # Never let messaging failures break payment processing
            logger.error(
                "whatsapp_send_error",
                order_number=order.order_number,
                error=str(e),
            )

    # ------------------------------------------------------------------ #
    # Status handlers (Payment Agent + DB Agent working together)
    # ------------------------------------------------------------------ #

    async def handle_paid(
        self,
        merchant_ref: str,
        reference_number: str,
        payment_amount: float,
        payment_method: str | None,
        fawry_fees: float,
        raw_data: dict,
    ) -> OrderModel | None:
        """PAID: Fetch order → confirm order → mark payment paid."""
        order = await self._get_order_by_payment_id(merchant_ref)
        if not order:
            logger.warning("webhook_order_not_found", status="PAID", ref=merchant_ref)
            return None

        # Narrow RLS from bypass → tenant-scoped for all subsequent writes
        await narrow_to_tenant(self.db, order.tenant_id)

        # Guard: only transition from valid prior states
        if order.status not in _PAID_VALID_PRIOR:
            logger.info(
                "webhook_ignored_invalid_transition",
                status="PAID",
                order_number=order.order_number,
                current_status=order.status.value
                if hasattr(order.status, "value")
                else str(order.status),
            )
            return order

        # Payment Agent: state transitions
        order.status = OrderStatus.CONFIRMED
        order.payment_status = PaymentStatus.PAID
        order.paid_at = datetime.now(UTC)
        order.payment_method = payment_method or "fawry"
        order.extra_data = {
            **(order.extra_data or {}),
            "fawry_reference": reference_number,
            "fawry_fees_cents": int(fawry_fees * 100) if fawry_fees else 0,
        }

        await self.db.flush()

        # Audit Agent
        await self._create_audit_log(
            event_type="payment.paid",
            order=order,
            details={
                "fawry_reference": reference_number,
                "amount": payment_amount,
                "method": payment_method,
                "fawry_fees": fawry_fees,
            },
        )

        logger.info(
            "order_paid",
            order_number=order.order_number,
            fawry_reference=reference_number,
            amount=payment_amount,
        )
        return order

    async def handle_expired(
        self,
        merchant_ref: str,
        raw_data: dict,
    ) -> OrderModel | None:
        """EXPIRED: Mark payment expired → release reserved inventory."""
        order = await self._get_order_by_payment_id(merchant_ref)
        if not order:
            logger.warning(
                "webhook_order_not_found", status="EXPIRED", ref=merchant_ref
            )
            return None

        await narrow_to_tenant(self.db, order.tenant_id)

        # Guard: only transition from valid prior states
        if order.status not in _EXPIRED_VALID_PRIOR:
            logger.info(
                "webhook_ignored_invalid_transition",
                status="EXPIRED",
                order_number=order.order_number,
                current_status=order.status.value
                if hasattr(order.status, "value")
                else str(order.status),
            )
            return order

        # Payment Agent
        order.payment_status = PaymentStatus.FAILED
        order.status = OrderStatus.PAYMENT_FAILED
        order.extra_data = {
            **(order.extra_data or {}),
            "payment_expired_at": datetime.now(UTC).isoformat(),
        }

        await self.db.flush()

        # Inventory Agent: release reserved stock
        await self._release_inventory(order)

        # Audit Agent
        await self._create_audit_log(
            event_type="payment.expired",
            order=order,
            details={"merchant_ref": merchant_ref},
        )

        logger.info(
            "order_payment_expired",
            order_number=order.order_number,
        )
        return order

    async def handle_canceled(
        self,
        merchant_ref: str,
        raw_data: dict,
    ) -> OrderModel | None:
        """CANCELED: Cancel order → mark payment failed → notify merchant."""
        order = await self._get_order_by_payment_id(merchant_ref)
        if not order:
            logger.warning(
                "webhook_order_not_found", status="CANCELED", ref=merchant_ref
            )
            return None

        await narrow_to_tenant(self.db, order.tenant_id)

        # Guard: shipped/delivered/refunded orders cannot be cancelled
        if order.status not in _CANCELED_VALID_PRIOR:
            logger.info(
                "webhook_ignored_invalid_transition",
                status="CANCELED",
                order_number=order.order_number,
                current_status=order.status.value
                if hasattr(order.status, "value")
                else str(order.status),
            )
            return order

        # Payment Agent
        order.status = OrderStatus.CANCELLED
        order.payment_status = PaymentStatus.FAILED
        order.cancelled_at = datetime.now(UTC)
        order.extra_data = {
            **(order.extra_data or {}),
            "cancellation_source": "fawry_webhook",
        }

        await self.db.flush()

        # Messaging Agent: notify merchant
        await self._notify_merchant_cancelled(order)

        # Audit Agent
        await self._create_audit_log(
            event_type="payment.canceled",
            order=order,
            details={"merchant_ref": merchant_ref, "source": "fawry_webhook"},
        )

        logger.info(
            "order_cancelled",
            order_number=order.order_number,
            source="fawry_webhook",
        )
        return order

    async def handle_refunded(
        self,
        merchant_ref: str,
        payment_amount: float,
        raw_data: dict,
    ) -> OrderModel | None:
        """REFUNDED: Mark payment refunded → create audit entry."""
        order = await self._get_order_by_payment_id(merchant_ref)
        if not order:
            logger.warning(
                "webhook_order_not_found", status="REFUNDED", ref=merchant_ref
            )
            return None

        await narrow_to_tenant(self.db, order.tenant_id)

        # Guard: can only refund a paid order
        if order.payment_status != PaymentStatus.PAID:
            logger.info(
                "webhook_ignored_invalid_transition",
                status="REFUNDED",
                order_number=order.order_number,
                current_payment_status=order.payment_status.value
                if hasattr(order.payment_status, "value")
                else str(order.payment_status),
            )
            return order

        # Payment Agent
        order.payment_status = PaymentStatus.REFUNDED
        order.extra_data = {
            **(order.extra_data or {}),
            "refunded_at": datetime.now(UTC).isoformat(),
            "refund_amount": payment_amount,
        }

        await self.db.flush()

        # Audit Agent — explicit event type per acceptance criteria
        await self._create_audit_log(
            event_type="payment.refunded",
            order=order,
            details={
                "merchant_ref": merchant_ref,
                "refund_amount": payment_amount,
            },
        )

        logger.info(
            "order_refunded",
            order_number=order.order_number,
            amount=payment_amount,
        )
        return order

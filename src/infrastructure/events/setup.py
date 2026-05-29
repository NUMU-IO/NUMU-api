"""Event bus factory - creates and wires the application event bus.

Called once at application startup to register all event handlers.
"""

from src.core.events.base import EventBus
from src.core.events.order_events import (
    OrderCreatedEvent,
    OrderPaidEvent,
    OrderStatusChangedEvent,
)
from src.core.events.payment_events import (
    PaymentProofApprovedEvent,
    PaymentProofRejectedEvent,
)
from src.core.events.product_events import (
    ProductCreatedEvent,
    ProductDeletedEvent,
    ProductUpdatedEvent,
)
from src.core.events.promotion_events import (
    PromotionCreatedEvent,
    PromotionDeletedEvent,
    PromotionUpdatedEvent,
)
from src.core.events.recovery_events import (
    RecoveryAbandonedEvent,
    RecoveryStartedEvent,
    RecoverySucceededEvent,
)
from src.core.events.risk_events import RiskAssessmentFinalisedEvent
from src.core.events.staff_events import (
    AccessRequestApprovedEvent,
    AccessRequestCreatedEvent,
    AccessRequestDeniedEvent,
    PermissionOverrideClearedEvent,
    PermissionOverrideSetEvent,
    StaffActivatedEvent,
    StaffInvitedEvent,
    StaffRemovedEvent,
    StaffRoleAssignedEvent,
    StaffRoleRevokedEvent,
    TemporaryAccessGrantedEvent,
    TemporaryAccessRevokedEvent,
)
from src.infrastructure.events.handlers.activity_log_handler import handle_activity_log
from src.infrastructure.events.handlers.email_notification_handler import (
    handle_email_notification,
)
from src.infrastructure.events.handlers.flow_trigger_handler import (
    handle_recovery_abandoned_for_flow_trigger,
    handle_recovery_succeeded_for_flow_trigger,
    handle_risk_finalised_for_flow_trigger,
)
from src.infrastructure.events.handlers.instapay_notification_handler import (
    handle_payment_proof_approved,
    handle_payment_proof_rejected,
    handle_whatsapp_payment_proof_approved,
    handle_whatsapp_payment_proof_rejected,
)
from src.infrastructure.events.handlers.invoice_on_paid_handler import (
    handle_invoice_on_order_paid,
)
from src.infrastructure.events.handlers.meta_capi_status_event_handler import (
    handle_order_status_changed_for_meta_capi,
)
from src.infrastructure.events.handlers.order_activity_handler import (
    handle_order_created_activity,
    handle_order_paid_activity,
    handle_order_status_changed_activity,
)
from src.infrastructure.events.handlers.otp_trust_handler import (
    handle_otp_verified_trust_signal,
)
from src.infrastructure.events.handlers.promotion_cache_invalidator import (
    PromotionCacheInvalidator,
)
from src.infrastructure.events.handlers.promotion_convert_handler import (
    handle_promotion_convert_on_order_paid,
)
from src.infrastructure.events.handlers.recovery_event_handler import (
    handle_recovery_started_for_celery,
    handle_recovery_succeeded_outbox,
    handle_risk_finalised_for_recovery,
)
from src.infrastructure.events.handlers.shipment_handler import (
    handle_order_status_for_shipment,
)
from src.infrastructure.events.handlers.staff_event_handlers import (
    handle_access_request_approved,
    handle_access_request_created,
    handle_access_request_denied,
    handle_override_cleared,
    handle_override_set,
    handle_staff_activated,
    handle_staff_invited,
    handle_staff_removed,
    handle_staff_role_assigned,
    handle_staff_role_revoked,
    handle_temporary_access_granted,
    handle_temporary_access_revoked,
)
from src.infrastructure.events.handlers.trust_signal_handler import (
    handle_recovery_succeeded_trust_signal,
)
from src.infrastructure.events.handlers.webhook_handler import (
    handle_webhook_order_created,
    handle_webhook_order_paid,
    handle_webhook_order_status_changed,
    handle_webhook_product_created,
    handle_webhook_product_deleted,
    handle_webhook_product_updated,
)
from src.infrastructure.events.handlers.whatsapp_notification_handler import (
    handle_order_created_whatsapp,
    handle_order_paid_whatsapp,
    handle_whatsapp_notification,
)
from src.infrastructure.events.handlers.whatsapp_scheduled_cancel_handler import (
    handle_order_status_for_scheduled_cancel,
)

# Module-level singleton
_event_bus: EventBus | None = None


def create_event_bus() -> EventBus:
    "Create and wire the global event bus (idempotent)."
    global _event_bus
    if _event_bus is not None:
        return _event_bus

    bus = EventBus()

    # Defer handler dispatch until the request transaction commits so handlers
    # that open their own session see just-written rows (fixes the order
    # activity FK race). Falls back to immediate dispatch outside a request.
    from src.infrastructure.events.deferred_dispatch import deferred_scheduler

    bus.scheduler = deferred_scheduler

    # Order status change - notifications + activity log + webhook
    bus.subscribe(OrderStatusChangedEvent, handle_email_notification)
    bus.subscribe(OrderStatusChangedEvent, handle_whatsapp_notification)
    bus.subscribe(OrderStatusChangedEvent, handle_activity_log)
    bus.subscribe(OrderStatusChangedEvent, handle_order_status_changed_activity)
    bus.subscribe(OrderStatusChangedEvent, handle_webhook_order_status_changed)
    # backend-030 / US3 — cascade-cancel any pending WhatsApp scheduled
    # sends linked to an order when that order moves to cancelled/refunded.
    bus.subscribe(OrderStatusChangedEvent, handle_order_status_for_scheduled_cancel)

    # Auto-create shipment on order confirmation
    bus.subscribe(OrderStatusChangedEvent, handle_order_status_for_shipment)

    # Wave 2 Phase 12 — fire Meta CAPI Purchase/Lead based on per-store
    # purchase_trigger / lead_trigger config (COD-aware timing).
    bus.subscribe(OrderStatusChangedEvent, handle_order_status_changed_for_meta_capi)

    # Order lifecycle webhooks + merchant-visible activity stream
    bus.subscribe(OrderCreatedEvent, handle_webhook_order_created)
    bus.subscribe(OrderCreatedEvent, handle_order_created_activity)
    # backend-030 / US1 — WhatsApp order-confirmation on order creation
    bus.subscribe(OrderCreatedEvent, handle_order_created_whatsapp)
    bus.subscribe(OrderPaidEvent, handle_webhook_order_paid)
    bus.subscribe(OrderPaidEvent, handle_order_paid_activity)
    # backend-030 / US1 — WhatsApp payment-received on order payment
    bus.subscribe(OrderPaidEvent, handle_order_paid_whatsapp)
    # offers-v2: emit `convert` PromotionEvent for every promotion
    # attributable to a paid order so merchant analytics show real
    # conversion totals (not just redemptions).
    bus.subscribe(OrderPaidEvent, handle_promotion_convert_on_order_paid)
    # Issue the ETA invoice + email PDF when the merchant marks a COD
    # order paid (or a future payment-gateway webhook fires OrderPaidEvent).
    bus.subscribe(OrderPaidEvent, handle_invoice_on_order_paid)

    # InstaPay proof lifecycle — short customer confirmation / rejection
    # emails that fire independently of the invoice handler so they
    # still land even if PDF generation fails. WhatsApp handlers run
    # in parallel on the same events so the primary channel in the EG
    # market never depends on email deliverability.
    bus.subscribe(PaymentProofApprovedEvent, handle_payment_proof_approved)
    bus.subscribe(PaymentProofRejectedEvent, handle_payment_proof_rejected)
    bus.subscribe(PaymentProofApprovedEvent, handle_whatsapp_payment_proof_approved)
    bus.subscribe(PaymentProofRejectedEvent, handle_whatsapp_payment_proof_rejected)

    # Product webhooks
    bus.subscribe(ProductCreatedEvent, handle_webhook_product_created)
    bus.subscribe(ProductUpdatedEvent, handle_webhook_product_updated)
    bus.subscribe(ProductDeletedEvent, handle_webhook_product_deleted)

    # Recovery flow (backend-021): risk-finalised → spawn flow; flow-started →
    # schedule first Celery send-step; flow-succeeded → outbox the Shopify
    # additive mutation.
    bus.subscribe(RiskAssessmentFinalisedEvent, handle_risk_finalised_for_recovery)
    bus.subscribe(RecoveryStartedEvent, handle_recovery_started_for_celery)
    bus.subscribe(RecoverySucceededEvent, handle_recovery_succeeded_outbox)
    # Backend-022: positive network signal contribution when a recovery succeeds.
    bus.subscribe(RecoverySucceededEvent, handle_recovery_succeeded_trust_signal)
    # Backend-020: Shopify Flow trigger emissions (idempotent via Celery task).
    bus.subscribe(RiskAssessmentFinalisedEvent, handle_risk_finalised_for_flow_trigger)
    bus.subscribe(RecoverySucceededEvent, handle_recovery_succeeded_for_flow_trigger)
    bus.subscribe(RecoveryAbandonedEvent, handle_recovery_abandoned_for_flow_trigger)
    # Backend-025 / spec 015: WhatsApp OTP success contributes a positive
    # network signal (lifts customer_trust on subsequent risk assessments).
    from src.core.events.otp_events import OtpVerifiedEvent

    bus.subscribe(OtpVerifiedEvent, handle_otp_verified_trust_signal)

    # Promotions — cache invalidation. The Redis client is fetched lazily
    # so import-time test environments without Redis don't crash.
    try:
        import redis.asyncio as _redis_async

        from src.config import settings as _settings
        from src.infrastructure.cache.promotion_cache import PromotionCache

        _promo_cache = PromotionCache(
            _redis_async.from_url(
                _settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        )
        _promo_invalidator = PromotionCacheInvalidator(_promo_cache)
        bus.subscribe(PromotionCreatedEvent, _promo_invalidator.on_created)
        bus.subscribe(PromotionUpdatedEvent, _promo_invalidator.on_updated)
        bus.subscribe(PromotionDeletedEvent, _promo_invalidator.on_deleted)
    except Exception as exc:  # noqa: BLE001 — startup tolerates missing Redis
        logger = __import__("logging").getLogger(__name__)
        logger.warning(
            "promotion_cache_invalidator_disabled error=%r — promo cache will only "
            "self-expire on TTL",
            exc,
        )

    # Staff events - invalidate cache + activity log + notifications
    bus.subscribe(StaffInvitedEvent, handle_staff_invited)
    bus.subscribe(StaffActivatedEvent, handle_staff_activated)
    bus.subscribe(StaffRoleAssignedEvent, handle_staff_role_assigned)
    bus.subscribe(StaffRoleRevokedEvent, handle_staff_role_revoked)
    bus.subscribe(StaffRemovedEvent, handle_staff_removed)
    bus.subscribe(PermissionOverrideSetEvent, handle_override_set)
    bus.subscribe(PermissionOverrideClearedEvent, handle_override_cleared)
    bus.subscribe(AccessRequestCreatedEvent, handle_access_request_created)
    bus.subscribe(AccessRequestApprovedEvent, handle_access_request_approved)
    bus.subscribe(AccessRequestDeniedEvent, handle_access_request_denied)
    bus.subscribe(TemporaryAccessGrantedEvent, handle_temporary_access_granted)
    bus.subscribe(TemporaryAccessRevokedEvent, handle_temporary_access_revoked)

    _event_bus = bus
    return bus


def get_event_bus() -> EventBus:
    "Get the global event bus, creating it if needed."
    if _event_bus is None:
        return create_event_bus()
    return _event_bus

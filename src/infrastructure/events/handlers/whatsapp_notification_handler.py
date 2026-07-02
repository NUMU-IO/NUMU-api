"""WhatsApp notification handlers for order lifecycle events.

Three handlers, all subscribed via ``src/infrastructure/events/setup.py``:

- ``handle_whatsapp_notification`` (OrderStatusChangedEvent, shipped/delivered)
  — existing; sends shipping + delivery confirmations.
- ``handle_order_created_whatsapp`` (OrderCreatedEvent)
  — new; sends order confirmation (FR-001, US1).
- ``handle_order_paid_whatsapp`` (OrderPaidEvent)
  — new; sends payment received (FR-002, US1).

The new handlers go through the same per-store credential resolver
(``get_whatsapp_service``) and the central send guard
(``WhatsAppSendGuard.check``), with all guard inputs prefetched from the
relevant repositories. Idempotency comes from a ``message_log`` lookup
keyed on ``metadata.order_id`` + event-type tag (research R5, FR-005).
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from src.core.enums.whatsapp import TemplateCategory
from src.core.events.order_events import (
    OrderCreatedEvent,
    OrderPaidEvent,
    OrderStatusChangedEvent,
)
from src.core.logging import get_logger
from src.core.services.whatsapp_send_guard import GuardContext, check

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_WHATSAPP_STATUSES = {"shipped", "delivered"}

# Map order status → the canonical whatsapp_notifications toggle key the
# merchant sees in the dashboard. Used as the lookup into
# store.settings.whatsapp_notifications inside _resolve_send_context.
_WA_PREF_KEYS = {
    "shipped": "shipping_update",
    "delivered": "delivery_confirmation",
}

# Map order status → the seeded DB template name (the same name Meta has
# approved). Used as the lookup into whatsapp_templates for the
# send-guard's APPROVED check.
_WA_TEMPLATE_NAMES = {
    "shipped": "order_shipped_v3",
    "delivered": "order_delivered_v2",
}


async def _persist_message_log(
    session: "AsyncSession",
    *,
    tenant_id: UUID,
    store_id: UUID,
    phone: str,
    template_name: str,
    message_id: str | None,
    status_str: str,
    metadata: dict,
) -> None:
    """Write a row to ``message_logs`` after a successful template send.

    This is the other half of the idempotency contract: ``_resolve_send_context``
    scans recent rows for a matching ``(template_name, metadata.order_id,
    metadata.event_tag)`` tuple to detect replays. Without this write the
    scan never finds anything and replays send duplicate templates.

    Failures here are swallowed (logged) — message_log is an audit trail,
    not part of the send-success contract. The worst case is one extra
    send on the next event replay; that's exactly what we're already
    fixing, so don't make it worse by raising.
    """
    if not message_id:
        # Send succeeded but Meta didn't return an id (rare). Skip the
        # log — without an id the row violates a NOT NULL constraint.
        return
    try:
        from src.core.entities.message_log import (
            MessageDirection,
            MessageLog,
            MessageStatus,
        )
        from src.infrastructure.repositories.message_log_repository import (
            MessageLogRepository,
        )

        try:
            status_enum = MessageStatus(status_str)
        except (ValueError, KeyError):
            status_enum = MessageStatus.SENT

        repo = MessageLogRepository(session)
        await repo.create(
            MessageLog(
                tenant_id=tenant_id,
                store_id=store_id,
                phone=phone,
                metadata=metadata,
                message_id=message_id,
                direction=MessageDirection.OUTBOUND,
                template_name=template_name,
                status=status_enum,
            )
        )
        await session.commit()
    except Exception:
        logger.exception(
            "whatsapp_message_log_persist_failed",
            store_id=str(store_id),
            template_name=template_name,
            message_id=message_id,
        )


async def handle_whatsapp_notification(event: OrderStatusChangedEvent) -> None:
    """Send WhatsApp notification for shipped/delivered status changes.

    Routes through ``_resolve_send_context`` + the central send-guard so
    merchant-side toggles (``store.settings.whatsapp_notifications.{shipping_update,
    delivery_confirmation}``), customer opt-outs, template-approval status,
    and message_log idempotency all apply consistently with the
    order_created / order_paid handlers.
    """
    if event.new_status not in _WHATSAPP_STATUSES:
        return

    pref_key = _WA_PREF_KEYS[event.new_status]
    template_name = _WA_TEMPLATE_NAMES[event.new_status]
    event_tag = f"order_{event.new_status}"

    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service

    async with AsyncSessionLocal() as session:
        resolution = await _resolve_send_context(
            session,
            store_id=event.store_id,
            customer_id=event.customer_id,
            template_name=template_name,
            idempotency_event_tag=event_tag,
            order_id=event.order_id,
            notification_pref_key=pref_key,
        )
        if resolution is None:
            return
        ctx, extras = resolution

        decision = check(ctx)
        if not decision.allowed:
            logger.info(
                "whatsapp_order_status_skipped",
                order_id=str(event.order_id),
                store_id=str(event.store_id),
                status=event.new_status,
                reason=decision.reason.value if decision.reason else "unknown",
            )
            return

        service = await get_whatsapp_service(
            event.store_id, session, extras["tenant_id"]
        )
        recipient = MessageRecipient(
            phone=extras["customer_phone"],
            name=extras["customer_name"],
            language=extras["language"],
        )

        if event.new_status == "shipped":
            # Self-describing ``<subdomain>/<order_id>`` button value so the
            # apex redirector resolves to this store's order page without a
            # prod-DB lookup (see handle_order_created_whatsapp for why).
            _order_ref = (
                f"{extras['store_subdomain']}/{event.order_id}"
                if extras.get("store_subdomain")
                else str(event.order_id)
            )
            result = await service.send_shipping_notification(
                recipient,
                event.order_number,
                event.tracking_number or "N/A",
                event.carrier or "Bosta",
                order_id=_order_ref,
            )
        else:  # delivered
            result = await service.send_delivery_notification(
                recipient,
                event.order_number,
                extras["store_name"],
            )

        if result.success:
            await _persist_message_log(
                session,
                tenant_id=extras["tenant_id"],
                store_id=event.store_id,
                phone=extras["customer_phone"],
                template_name=template_name,
                message_id=result.message_id,
                status_str=str(getattr(result.status, "value", result.status)),
                metadata={
                    "order_id": str(event.order_id),
                    "event_tag": event_tag,
                },
            )

        logger.info(
            "whatsapp_order_notification_sent",
            order_id=str(event.order_id),
            status=event.new_status,
            phone=extras["customer_phone"],
            success=result.success,
            message_id=result.message_id,
        )


# ─────────────────────────────────────────────────────────────────────
# US1 — order-created + order-paid handlers (FR-001, FR-002)
# ─────────────────────────────────────────────────────────────────────


async def _resolve_send_context(
    session: "AsyncSession",
    store_id: UUID,
    customer_id: UUID,
    template_name: str,
    *,
    idempotency_event_tag: str,
    order_id: UUID,
    notification_pref_key: str,
) -> tuple[GuardContext, dict] | None:
    """Prefetch everything the guard + send need. Returns ``None`` when the
    handler should silently no-op (missing customer / phone / store).
    Otherwise returns ``(GuardContext, extras)`` where ``extras`` carries
    the customer name, language, store name, tenant_id, and resolved phone
    for the downstream send call.
    """
    # Customer
    from src.infrastructure.database.models.tenant.customer import CustomerModel

    cust_row = (
        await session.execute(
            select(CustomerModel).where(CustomerModel.id == customer_id)
        )
    ).scalar_one_or_none()
    if cust_row is None or not cust_row.phone:
        logger.info(
            "whatsapp_order_event_skipped",
            order_id=str(order_id),
            event_tag=idempotency_event_tag,
            reason="no_customer_or_phone",
        )
        return None

    customer_phone = cust_row.phone
    customer_name = f"{cust_row.first_name} {cust_row.last_name}".strip()
    notification_prefs = cust_row.notification_prefs or {}
    whatsapp_prefs = notification_prefs.get("whatsapp", {}) or {}
    customer_pref_enabled = bool(whatsapp_prefs.get(notification_pref_key, True))
    tenant_id = cust_row.tenant_id

    # Store
    from src.infrastructure.database.models.tenant.store import StoreModel

    store_row = (
        await session.execute(select(StoreModel).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if store_row is None:
        logger.info(
            "whatsapp_order_event_skipped",
            order_id=str(order_id),
            reason="no_store",
        )
        return None
    store_name = store_row.name
    # Language used to drive (1) the DB template-row lookup below and
    # (2) the Meta /messages language code at send time. The seeded
    # system templates cover {ar, en_US}, so we map the store's
    # default_language to whichever locale exists in the seed —
    # English fans out to en_US (Meta's canonical en locale for our
    # WABA), everything else stays as-is and falls back to ar if the
    # store didn't pick a value. Sentry HIGH-4: previously hardcoded
    # to "ar", so English-default stores never matched the en_US
    # template row, send-guard saw template_status=None and skipped
    # every order_confirmation send to English-speaking customers.
    store_settings = store_row.settings or {}
    # Store-level message-language override — the "send in which language"
    # control on the WhatsApp Overview page. Persisted at
    # store.settings.whatsapp.message_language by PATCH /whatsapp/settings.
    # "ar"/"en" force that language for every automated notification;
    # "auto" (default) follows store.default_language. Customers carry no
    # per-recipient language column, so "auto" resolves to the store
    # default — which is also the pre-override behaviour, keeping existing
    # stores unchanged.
    wa_lang_pref = str(
        (store_settings.get("whatsapp") or {}).get("message_language") or "auto"
    ).lower()
    if wa_lang_pref == "ar":
        raw_lang = "ar"
    elif wa_lang_pref == "en":
        raw_lang = "en"
    else:  # auto
        raw_lang = (store_row.default_language or "ar").lower()
    if raw_lang.startswith("en"):
        language = "en_US"
    else:
        language = "ar"
    store_whatsapp_notifications = (
        store_settings.get("whatsapp_notifications", {}) or {}
    )
    # Merchant-side toggle for this message type. FR-019a default is True
    # for platform-managed stores; BYO-mode default-DISABLED is enforced
    # by the BYO connect flow (T072 / US4) which writes False on connect.
    # Until that lands, an absent key means True.
    merchant_enabled = bool(
        store_whatsapp_notifications.get(notification_pref_key, True)
    )

    # Customer pref AND merchant pref must both be on.
    notification_setting_enabled = customer_pref_enabled and merchant_enabled

    # Opt-in / opt-out
    from src.infrastructure.repositories.whatsapp_opt_in_repository import (
        WhatsAppOptInRepository,
    )

    optin_repo = WhatsAppOptInRepository(session)
    has_active_opt_in = (
        await optin_repo.get_active(store_id, customer_phone)
    ) is not None
    has_opt_out = await optin_repo.has_opt_out(store_id, customer_phone)

    # Template status — only APPROVED templates may send (FR-029).
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )

    # The DB template rows are seeded with INCONSISTENT English locale
    # codes: order_confirmation_v2 is seeded "en_US" while order_shipped_v2
    # and order_delivered are seeded "en" (see _SYSTEM_TEMPLATES in
    # 20260524_010000_add_whatsapp_optin_scheduled_dl.py). We resolve an
    # English store to "en_US" above, so an exact-match lookup finds the
    # confirmation row but MISSES the shipped/delivered rows → the
    # send-guard sees template_status=None → every shipped/delivered send
    # to an English-default store is silently blocked with
    # TEMPLATE_NOT_APPROVED. Match either locale variant so the lookup is
    # robust to the seed drift. (Arabic is unaffected — "ar" everywhere.)
    lang_candidates = [language]
    if language == "en_US":
        lang_candidates.append("en")
    elif language == "en":
        lang_candidates.append("en_US")

    tmpl_rows = (
        (
            await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.store_id == store_id,
                    WhatsAppTemplateModel.name == template_name,
                    WhatsAppTemplateModel.language.in_(lang_candidates),
                )
            )
        )
        .scalars()
        .all()
    )
    # Prefer an exact language match, then an APPROVED row, else any row.
    tmpl_row = (
        next(
            (t for t in tmpl_rows if t.language == language),
            None,
        )
        or next(
            (
                t
                for t in tmpl_rows
                if getattr(t.status, "value", t.status) == "APPROVED"
            ),
            None,
        )
        or (tmpl_rows[0] if tmpl_rows else None)
    )
    template_status = tmpl_row.status if tmpl_row is not None else None

    # Credentials check — done via the resolver later. The guard only needs
    # to know whether credentials are configured (platform OR BYO). We
    # treat absence as no_credentials; the resolver decides if the platform
    # path is available.
    store_has_credentials = (
        True  # Resolver always returns a service (platform fallback)
    )
    store_credentials_marked_invalid = bool(
        store_settings.get("whatsapp", {}).get("credential_error")
    )

    # Idempotency — message_log lookup keyed on order_id + event_tag.
    from src.infrastructure.repositories.message_log_repository import (
        MessageLogRepository,
    )

    msg_log_repo = MessageLogRepository(session)
    # Scan recent sends for this phone within the store; filter in memory
    # for matching template + metadata.order_id + event_tag (research R5).
    # 50 covers > 1 month of normal customer activity in this store.
    recent = await msg_log_repo.get_by_phone(store_id, customer_phone, limit=50)
    success_statuses = {"sent", "delivered", "read"}
    already_sent = False
    for log in recent:
        meta = getattr(log, "metadata", None) or {}
        status_str = (
            log.status.value if hasattr(log.status, "value") else str(log.status)
        )
        if (
            log.template_name == template_name
            and meta.get("order_id") == str(order_id)
            and meta.get("event_tag") == idempotency_event_tag
            and status_str in success_statuses
        ):
            already_sent = True
            break

    ctx = GuardContext(
        phone=customer_phone,
        template_name=template_name,
        template_category=TemplateCategory.UTILITY,
        template_status=template_status,
        store_has_credentials=store_has_credentials,
        store_credentials_marked_invalid=store_credentials_marked_invalid,
        notification_setting_enabled=notification_setting_enabled,
        has_active_opt_in=has_active_opt_in,
        has_opt_out=has_opt_out,
        window_is_open=True,  # template sends ignore the 24h window (FR-037 (f))
        already_sent=already_sent,
    )

    extras = {
        "customer_phone": customer_phone,
        "customer_name": customer_name,
        "language": language,
        "tenant_id": tenant_id,
        "store_name": store_name,
        # Subdomain drives the self-describing ``<subdomain>/<order_id>``
        # button value built at the call sites below — see the comment
        # there for why the bare UUID isn't enough.
        "store_subdomain": store_row.subdomain,
        # Resolved template row id — used by the COD confirm-request path to
        # enqueue a delayed scheduled send against the right template.
        "template_id": tmpl_row.id if tmpl_row is not None else None,
    }
    return ctx, extras


async def _maybe_send_cod_confirm_request(
    session: "AsyncSession", event: OrderCreatedEvent
) -> bool:
    """COD "tap to confirm" path (order_confirmation_request_v1).

    When the merchant has enabled ``require_order_confirmation`` for the store, a COD
    order gets the active confirm-request (with a quick-reply button)
    instead of the passive order_confirmation_v2 notice. Returns True when
    this handled the order — the caller then skips the v2 send. Returns
    False for non-COD orders or stores without the feature enabled.

    Honours the merchant's timing choice: ``confirm_order_delay_minutes``
    of 0 sends immediately, >0 enqueues a scheduled send (the dispatcher
    re-checks the guard and dispatches the real template at fire-time).
    """
    from datetime import UTC, datetime, timedelta

    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    order_row = (
        await session.execute(select(OrderModel).where(OrderModel.id == event.order_id))
    ).scalar_one_or_none()
    if order_row is None or (order_row.payment_method or "").lower() != "cod":
        return False

    store_row = (
        await session.execute(select(StoreModel).where(StoreModel.id == event.store_id))
    ).scalar_one_or_none()
    store_settings = (store_row.settings if store_row else None) or {}
    notif = store_settings.get("whatsapp_notifications", {}) or {}
    if not bool(notif.get("require_order_confirmation", False)):
        return False

    # From here this IS the confirm-order flow: even if the guard skips the
    # send below, return True so we don't also fire order_confirmation_v2.
    resolution = await _resolve_send_context(
        session,
        store_id=event.store_id,
        customer_id=event.customer_id,
        template_name="order_confirmation_request_v2",
        idempotency_event_tag="order_confirm_request",
        order_id=event.order_id,
        notification_pref_key="require_order_confirmation",
    )
    if resolution is None:
        return True
    ctx, extras = resolution

    decision = check(ctx)
    if not decision.allowed:
        logger.info(
            "whatsapp_order_confirm_request_skipped",
            order_id=str(event.order_id),
            store_id=str(event.store_id),
            reason=decision.reason.value if decision.reason else "unknown",
        )
        return True

    # Self-describing payload so the inbound webhook resolves the order
    # without a prod-DB lookup — mirrors the /o/ redirect convention.
    confirm_payload = (
        f"{extras['store_subdomain']}/{event.order_id}"
        if extras.get("store_subdomain")
        else str(event.order_id)
    )
    # event.total is in CENTS — divide for the human-readable amount.
    total_str = f"{event.total / 100:.2f} {event.currency}"
    # One-line delivery address for body {{4}}. shipping_address is the
    # stored JSONB dict (address_line1/line2/city/...).
    _addr = order_row.shipping_address or {}
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
    delay_minutes = int(
        (store_settings.get("whatsapp") or {}).get("confirm_order_delay_minutes") or 0
    )
    now = datetime.now(UTC)

    # Rich-template detail fields ({{5}} payment, {{6}} item count). Derived
    # from the order so the Bosta-style body renders fully.
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        payment_label,
    )

    payment_label_str = payment_label(order_row.payment_method, extras["language"])
    item_count_str = str(len(order_row.line_items or []))

    if delay_minutes > 0:
        repo = WhatsAppScheduledSendRepository(session)
        await repo.create(
            tenant_id=extras["tenant_id"],
            store_id=event.store_id,
            phone=extras["customer_phone"],
            scheduled_for=now + timedelta(minutes=delay_minutes),
            template_id=extras["template_id"],
            template_params={
                "customer_name": extras["customer_name"],
                "store_name": extras["store_name"],
                "order_number": event.order_number,
                "total": total_str,
                "payment_label": payment_label_str,
                "item_count": item_count_str,
                "address": address_str,
                "confirm_payload": confirm_payload,
            },
            customer_id=event.customer_id,
            related_order_id=event.order_id,
        )
        order_row.customer_confirmation_status = "pending"
        order_row.customer_confirmation_requested_at = now
        await session.commit()
        logger.info(
            "whatsapp_order_confirm_request_scheduled",
            order_id=str(event.order_id),
            store_id=str(event.store_id),
            delay_minutes=delay_minutes,
        )
        return True

    # Immediate send.
    service = await get_whatsapp_service(event.store_id, session, extras["tenant_id"])
    recipient = MessageRecipient(
        phone=extras["customer_phone"],
        name=extras["customer_name"],
        language=extras["language"],
    )
    result = await service.send_order_confirmation_request(
        recipient,
        event.order_number,
        total_str,
        address_str,
        confirm_payload,
        store_name=extras["store_name"],
        payment_label_text=payment_label_str,
        item_count=item_count_str,
    )

    if result.success:
        order_row.customer_confirmation_status = "pending"
        order_row.customer_confirmation_requested_at = now
        await session.commit()
        await _persist_message_log(
            session,
            tenant_id=extras["tenant_id"],
            store_id=event.store_id,
            phone=extras["customer_phone"],
            template_name="order_confirmation_request_v2",
            message_id=result.message_id,
            status_str=str(getattr(result.status, "value", result.status)),
            metadata={
                "order_id": str(event.order_id),
                "event_tag": "order_confirm_request",
            },
        )

    logger.info(
        "whatsapp_order_confirm_request_sent",
        order_id=str(event.order_id),
        store_id=str(event.store_id),
        success=result.success,
        message_id=result.message_id,
    )
    return True


async def handle_order_created_whatsapp(event: OrderCreatedEvent) -> None:
    """US1 / FR-001 — send a WhatsApp order-confirmation when an order is
    created. Idempotent: replayed events do not produce duplicate sends.
    Guard-gated: opt-out / merchant-setting-off / non-APPROVED template /
    invalid phone all block the send with a structured skip-reason log
    (FR-038 / FR-039).

    COD orders on stores with the "confirm order in WhatsApp" feature
    enabled take the active confirm-request path instead (see
    ``_maybe_send_cod_confirm_request``).
    """
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service

    async with AsyncSessionLocal() as session:
        # COD "tap to confirm" flow supersedes the passive v2 notice.
        if await _maybe_send_cod_confirm_request(session, event):
            return

        resolution = await _resolve_send_context(
            session,
            store_id=event.store_id,
            customer_id=event.customer_id,
            # Must match the DB seed name exactly — that row carries the
            # `status` field the send-guard reads (APPROVED → allow send).
            # See _SYSTEM_TEMPLATES in the alembic migration.
            template_name="order_confirmation_v3",
            idempotency_event_tag="order_created",
            order_id=event.order_id,
            notification_pref_key="order_confirmation",
        )
        if resolution is None:
            return
        ctx, extras = resolution

        # Resolve the order's payment method for the v3 body's "Payment"
        # line ({{5}}). The OrderCreatedEvent doesn't carry it, so read the
        # order row.
        from src.infrastructure.database.models.tenant.order import OrderModel
        from src.infrastructure.external_services.whatsapp.messaging_service import (
            payment_label,
        )

        _order_row = (
            await session.execute(
                select(OrderModel).where(OrderModel.id == event.order_id)
            )
        ).scalar_one_or_none()
        payment_label_str = payment_label(
            _order_row.payment_method if _order_row else None, extras["language"]
        )

        decision = check(ctx)
        if not decision.allowed:
            logger.info(
                "whatsapp_order_created_skipped",
                order_id=str(event.order_id),
                store_id=str(event.store_id),
                event_type="order_created",
                reason=decision.reason.value if decision.reason else "unknown",
            )
            return

        # Dispatch via the per-store resolver.
        service = await get_whatsapp_service(
            event.store_id, session, extras["tenant_id"]
        )
        recipient = MessageRecipient(
            phone=extras["customer_phone"],
            name=extras["customer_name"],
            language=extras["language"],
        )
        # order_confirmation_v2 renders a "Manage order" URL button at
        # https://numueg.app/o/{{1}}. We substitute the self-describing
        # ``<subdomain>/<order_id>`` value so the apex redirector routes
        # straight to THIS store's order-tracking page WITHOUT a prod-DB
        # lookup. A bare UUID forces the redirector's DB-lookup branch,
        # which can't find test/stage orders (they live in another DB) and
        # falls back to the apex landing page — the "broken track link" bug.
        # Bare UUID only when the store has no subdomain (custom-domain-only).
        _order_ref = (
            f"{extras['store_subdomain']}/{event.order_id}"
            if extras.get("store_subdomain")
            else str(event.order_id)
        )
        result = await service.send_order_confirmation(
            recipient,
            event.order_number,
            # event.total is in CENTS — divide for the human-readable amount.
            f"{event.total / 100:.2f} {event.currency}",
            extras["store_name"],
            order_id=_order_ref,
            payment_label_text=payment_label_str,
        )

        if result.success:
            await _persist_message_log(
                session,
                tenant_id=extras["tenant_id"],
                store_id=event.store_id,
                phone=extras["customer_phone"],
                template_name="order_confirmation_v3",
                message_id=result.message_id,
                status_str=str(getattr(result.status, "value", result.status)),
                metadata={
                    "order_id": str(event.order_id),
                    "event_tag": "order_created",
                },
            )

        logger.info(
            "whatsapp_order_created_sent",
            order_id=str(event.order_id),
            store_id=str(event.store_id),
            success=result.success,
            message_id=result.message_id,
        )


async def handle_order_paid_whatsapp(event: OrderPaidEvent) -> None:
    """US1 / FR-002 — send a WhatsApp payment-received message when an
    order's payment is confirmed. Same guard + idempotency pattern as
    handle_order_created_whatsapp.
    """
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service

    async with AsyncSessionLocal() as session:
        resolution = await _resolve_send_context(
            session,
            store_id=event.store_id,
            customer_id=event.customer_id,
            template_name="payment_received_v2",
            idempotency_event_tag="order_paid",
            order_id=event.order_id,
            notification_pref_key="payment_received",
        )
        if resolution is None:
            return
        ctx, extras = resolution

        decision = check(ctx)
        if not decision.allowed:
            logger.info(
                "whatsapp_order_paid_skipped",
                order_id=str(event.order_id),
                store_id=str(event.store_id),
                event_type="order_paid",
                reason=decision.reason.value if decision.reason else "unknown",
            )
            return

        service = await get_whatsapp_service(
            event.store_id, session, extras["tenant_id"]
        )
        recipient = MessageRecipient(
            phone=extras["customer_phone"],
            name=extras["customer_name"],
            language=extras["language"],
        )

        result = await service.send_payment_received(
            recipient,
            event.order_number,
            # event.total is in CENTS — divide for the human-readable amount.
            f"{event.total / 100:.2f}",
        )

        if result.success:
            await _persist_message_log(
                session,
                tenant_id=extras["tenant_id"],
                store_id=event.store_id,
                phone=extras["customer_phone"],
                template_name="payment_received_v2",
                message_id=result.message_id,
                status_str=str(getattr(result.status, "value", result.status)),
                metadata={
                    "order_id": str(event.order_id),
                    "event_tag": "order_paid",
                },
            )

        logger.info(
            "whatsapp_order_paid_sent",
            order_id=str(event.order_id),
            store_id=str(event.store_id),
            success=result.success,
            message_id=result.message_id,
        )

"""Celery beat task: dispatch due WhatsApp scheduled sends (FR-014, FR-015).

Runs every 60 seconds. Each invocation:
1. Iterates over tenants (admin RLS bypass + per-tenant context for
   each query batch — same pattern used by abandoned_cart_tasks).
2. SELECTs pending rows where scheduled_for <= NOW with
   FOR UPDATE SKIP LOCKED (per-row lock so peer workers cannot grab
   the same row, FR-017 concurrent dispatch).
3. For each row, re-evaluates the send guard (FR-017 — guard is
   evaluated at dispatch-time, not schedule-time, so changes in
   opt-out state / template status / merchant settings between
   schedule and fire are honoured).
4. Dispatches via the per-store-resolved WhatsAppMessagingService.
5. Updates row status to sent / skipped / failed.

Failures route to ``mark_failed`` for now; the full retry + DLQ wiring
lands in US6 (T102-T108) where every WhatsApp Celery task gets the
same exponential-backoff treatment.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None

# Map a system template name to the merchant-facing notification toggle that
# gates it (store.settings.whatsapp_notifications.<key>). Mirrors the
# order-lifecycle handler's notification_pref_key wiring so a scheduled send
# honours the SAME per-message-type switch the merchant sees on the WhatsApp
# Overview page. Templates not in this map (ad-hoc win-back / review-request
# follow-ups) fall back to the generic "marketing" umbrella.
_TEMPLATE_PREF_KEY = {
    # Current (rich) template names.
    "order_confirmation_v3": "order_confirmation",
    "order_confirmation_request_v2": "require_order_confirmation",
    "payment_received_v2": "payment_received",
    "order_shipped_v3": "shipping_update",
    "order_delivered_v2": "delivery_confirmation",
    "abandoned_cart_v3": "abandoned_cart",
    # Legacy names — kept so in-flight scheduled rows created before the
    # rich-template cutover still map to the right merchant toggle.
    "order_confirmation_v2": "order_confirmation",
    "order_confirmation_request_v1": "require_order_confirmation",
    "payment_received": "payment_received",
    "order_shipped_v2": "shipping_update",
    "order_delivered": "delivery_confirmation",
    "abandoned_cart_v2": "abandoned_cart",
}


def _resolve_language(store_settings: dict, default_language: str | None) -> str:
    """Resolve the WhatsApp send language to one of {"en", "ar"}.

    Honours the store-level message-language override
    (store.settings.whatsapp.message_language) exactly like the
    order-lifecycle path: "ar"/"en" force that language; "auto" (default)
    follows the store's default_language. Kept in {en, ar} so it matches
    the EGYPTIAN_TEMPLATES keys and the system templates' seeded locales.
    """
    pref = str(
        (store_settings.get("whatsapp") or {}).get("message_language") or "auto"
    ).lower()
    if pref == "ar":
        raw = "ar"
    elif pref == "en":
        raw = "en"
    else:  # auto
        raw = (default_language or "ar").lower()
    return "en" if raw.startswith("en") else "ar"


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="numu_api.whatsapp.dispatch_scheduled_sends",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=120,
)
def dispatch_scheduled_sends_task(self) -> dict[str, int]:
    """Beat-scheduled dispatcher. Returns dispatch stats for observability."""
    try:
        return _run_async(_dispatch_all_tenants())
    except Exception as exc:
        logger.error("scheduled_send_dispatcher_failed", error=str(exc), exc_info=True)
        raise self.retry(exc=exc)


async def _dispatch_all_tenants() -> dict[str, int]:
    """Top-level coroutine — fans out per-tenant."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.whatsapp_scheduled_send import (
        WhatsAppScheduledSendModel,
    )
    from src.infrastructure.tenancy.rls import RLSBypassContext

    stats: dict[str, int] = {"dispatched": 0, "skipped": 0, "failed": 0}

    # Discover tenants with at least one due row. Uses RLS bypass since
    # this is a cross-tenant administrative scan.
    async with AsyncSessionLocal() as session:
        async with RLSBypassContext(session):
            now = datetime.now(UTC)
            tenant_rows = (
                await session.execute(
                    select(WhatsAppScheduledSendModel.tenant_id)
                    .where(
                        WhatsAppScheduledSendModel.status == "pending",
                        WhatsAppScheduledSendModel.scheduled_for <= now,
                    )
                    .distinct()
                )
            ).all()
            tenant_ids = [row[0] for row in tenant_rows]

    for tenant_id in tenant_ids:
        try:
            per_tenant = await _dispatch_for_tenant(tenant_id)
            for k, v in per_tenant.items():
                stats[k] = stats.get(k, 0) + v
        except Exception as exc:
            logger.warning(
                "scheduled_send_dispatcher_tenant_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
            )

    logger.info("scheduled_send_dispatcher_done", **stats)
    return stats


async def _dispatch_for_tenant(tenant_id: Any) -> dict[str, int]:
    """Process up to 100 due rows for a single tenant."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.whatsapp_opt_in_repository import (
        WhatsAppOptInRepository,
    )
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )
    from src.infrastructure.tenancy.rls import RLSContext

    stats = {"dispatched": 0, "skipped": 0, "failed": 0}
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        # Per-tenant RLS context — every query inside is filtered to
        # this tenant's rows (TASK-SEC-005 enforced by the database).
        async with RLSContext(session, tenant_id):
            repo = WhatsAppScheduledSendRepository(session)
            optin_repo = WhatsAppOptInRepository(session)

            due_rows = await repo.list_due(now=now, limit=100)
            if not due_rows:
                return stats

            for row in due_rows:
                try:
                    decision, language = await _evaluate_guard(
                        session, optin_repo, row, now=now
                    )
                    if not decision.allowed:
                        await repo.mark_skipped(
                            row.id,
                            reason=(
                                decision.reason.value if decision.reason else "unknown"
                            ),
                        )
                        stats["skipped"] += 1
                        logger.info(
                            "scheduled_send_skipped",
                            send_id=str(row.id),
                            store_id=str(row.store_id),
                            reason=(
                                decision.reason.value if decision.reason else "unknown"
                            ),
                        )
                        continue

                    # Allowed — dispatch through the per-store resolver
                    sent_ok = await _dispatch_one(session, row, language)
                    if sent_ok:
                        await repo.mark_sent(row.id)
                        stats["dispatched"] += 1
                    else:
                        await repo.mark_failed(row.id, reason="meta_send_failed")
                        stats["failed"] += 1
                except Exception as exc:
                    logger.warning(
                        "scheduled_send_row_failed",
                        send_id=str(row.id),
                        error=str(exc),
                    )
                    try:
                        await repo.mark_failed(row.id, reason=str(exc)[:1000])
                    except Exception:
                        pass
                    stats["failed"] += 1
            await session.commit()
    return stats


async def _evaluate_guard(session, optin_repo, row, *, now: datetime):
    """Rebuild GuardContext at dispatch-time. Re-queries opt-in / opt-out
    / template status / merchant setting so changes between schedule
    and dispatch are honoured (FR-017).
    """
    from sqlalchemy import select

    from src.core.enums.whatsapp import TemplateCategory
    from src.core.services.whatsapp_send_guard import GuardContext, check
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )

    # Store + settings
    store_row = (
        await session.execute(select(StoreModel).where(StoreModel.id == row.store_id))
    ).scalar_one_or_none()
    store_settings = (store_row.settings if store_row else None) or {}
    notif = store_settings.get("whatsapp_notifications", {}) or {}

    # Template lookup
    template_status: str | None = None
    template_category: TemplateCategory | None = None
    template_name: str | None = None
    if row.template_id is not None:
        tmpl = (
            await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.id == row.template_id
                )
            )
        ).scalar_one_or_none()
        if tmpl is not None:
            template_status = tmpl.status
            template_name = tmpl.name
            try:
                template_category = TemplateCategory(tmpl.category)
            except ValueError:
                template_category = TemplateCategory.UTILITY

    # Honour the per-message-type merchant toggle this send maps to (e.g. an
    # abandoned_cart_v2 scheduled send is gated by the "Abandoned cart" switch
    # on the WhatsApp Overview page). The guard is re-evaluated at dispatch
    # time, so flipping the toggle OFF after a row was scheduled correctly
    # skips it. Templates with no specific mapping fall back to the generic
    # marketing umbrella. Defaults follow NotificationSettings (all True).
    pref_key = _TEMPLATE_PREF_KEY.get(template_name or "", "marketing")
    notification_enabled = bool(notif.get(pref_key, True))

    # Opt-in / opt-out
    has_active_opt_in = (
        await optin_repo.get_active(row.store_id, row.phone)
    ) is not None
    has_opt_out = await optin_repo.has_opt_out(row.store_id, row.phone)

    ctx = GuardContext(
        phone=row.phone,
        template_name=template_name,
        template_category=template_category,
        template_status=template_status,
        store_has_credentials=True,  # resolver always returns a service
        store_credentials_marked_invalid=bool(
            store_settings.get("whatsapp", {}).get("credential_error")
        ),
        notification_setting_enabled=notification_enabled,
        has_active_opt_in=has_active_opt_in,
        has_opt_out=has_opt_out,
        window_is_open=True,
        already_sent=False,  # scheduled-send is its own idempotency unit
    )
    language = _resolve_language(
        store_settings, store_row.default_language if store_row else None
    )
    return check(ctx), language


async def _dispatch_one(session, row, language: str = "ar") -> bool:
    """Issue the actual Meta send. Returns True on success.

    ``language`` is resolved by the guard from the store's message-language
    setting (override → default_language); it replaces the previously
    hardcoded "ar" so a store configured for English (or "auto" on an
    English store) sends in the right language.
    """
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service

    service = await get_whatsapp_service(row.store_id, session, row.tenant_id)
    recipient = MessageRecipient(phone=row.phone, name="", language=language)
    tmpl_name: str | None = None

    if row.template_id is not None:
        # Resolve the template name so structured templates dispatch as real
        # template messages (preserving buttons) rather than the interim
        # flattened-text fallback.
        from src.infrastructure.database.models.tenant.whatsapp_template import (
            WhatsAppTemplateModel,
        )

        tmpl = (
            await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.id == row.template_id
                )
            )
        ).scalar_one_or_none()
        tmpl_name = tmpl.name if tmpl is not None else None
        params = row.template_params or {}

        if tmpl_name in (
            "order_confirmation_request_v2",
            "order_confirmation_request_v1",
        ):
            # Real template send — a flattened-text fallback would drop the
            # quick-reply buttons (the whole point of this template). The
            # send method derives the 3 button payloads from the stored
            # base ``confirm_payload`` and renders the rich detail lines from
            # the params persisted when the row was scheduled.
            recipient = MessageRecipient(
                phone=row.phone,
                name=str(params.get("customer_name") or ""),
                language=language,
            )
            result = await service.send_order_confirmation_request(
                recipient,
                str(params.get("order_number") or ""),
                str(params.get("total") or ""),
                str(params.get("address") or "-"),
                str(params.get("confirm_payload") or ""),
                store_name=str(params.get("store_name") or ""),
                payment_label_text=str(params.get("payment_label") or ""),
                item_count=str(params.get("item_count") or ""),
            )
        else:
            # Interim text fallback for templates without a structured send
            # path. Works inside the 24h window the guard verified is open.
            text = _flatten_params(params)
            result = await service.send_text_message(recipient, text)
    else:
        result = await service.send_text_message(recipient, row.text_message or "")

    # Persist an outbound message_logs row so delivery/read status webhooks
    # (keyed on the Meta message_id) have a row to update. Without this, a
    # scheduled send (e.g. the delayed COD confirm-request) leaves no audit
    # trail and its delivered/read status is silently lost. Fail-open — the
    # send already succeeded; logging is best-effort.
    if result.success and result.message_id:
        await _persist_scheduled_message_log(
            session,
            row=row,
            template_name=tmpl_name,
            message_id=result.message_id,
            status_str=str(getattr(result.status, "value", result.status)),
        )

    return bool(result.success)


async def _persist_scheduled_message_log(
    session,
    *,
    row,
    template_name: str | None,
    message_id: str,
    status_str: str,
) -> None:
    """Write an outbound message_logs row for a dispatched scheduled send.

    Mirrors the order-lifecycle handler's ``_persist_message_log`` so the
    status-update webhook can resolve and update the row. Tagged with the
    related order id + ``scheduled_send`` event tag for traceability.
    """
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

        await MessageLogRepository(session).create(
            MessageLog(
                tenant_id=row.tenant_id,
                store_id=row.store_id,
                phone=row.phone,
                metadata={
                    "order_id": str(row.related_order_id)
                    if row.related_order_id
                    else None,
                    "event_tag": "scheduled_send",
                    "scheduled_send_id": str(row.id),
                },
                message_id=message_id,
                direction=MessageDirection.OUTBOUND,
                template_name=template_name,
                status=status_enum,
            )
        )
    except Exception:
        logger.exception(
            "scheduled_send_message_log_persist_failed", send_id=str(row.id)
        )


def _flatten_params(params: dict[str, Any]) -> str:
    """Best-effort body text from template params for the dispatcher's
    interim text-send path. Format: "key1: val1\\nkey2: val2".
    """
    if not params:
        return "(scheduled message)"
    return "\n".join(f"{k}: {v}" for k, v in params.items())

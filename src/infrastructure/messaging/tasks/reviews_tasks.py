"""Reviews app: the request sweep and one request's send.

- ``sweep`` (every 10 minutes): for each store the app is live on, while its
  send window is open and its daily cap is not reached, queue the requests
  whose time has come. Requests wait days, so they are never Celery
  countdowns; this sweep is the only way they go out.
- ``send``: one WhatsApp message through core's WhatsApp messaging service,
  with a fresh link token whose hash goes on the request.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.application.services import reviews_service as svc
from src.application.services.app_install_gate import is_live
from src.core.logging import get_logger
from src.core.utils.store_timezone import resolve_store_timezone_name
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.reviews import (
    ReviewsRequestModel as Request,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.messaging.tasks.back_in_stock_app_tasks import _sending
from src.infrastructure.messaging.tasks.order_tracking_tasks import (
    _LATER,
    _RETRY_SECONDS,
    _STOP,
    SendLater,
    _e164,
    _run_async,
)
from src.infrastructure.repositories import reviews_repository as repo
from src.infrastructure.tenancy.rls import enable_rls_bypass

logger = get_logger(__name__)

#: Requests one store queues per sweep; the next sweep takes the rest.
BATCH = 50


# ─── sweep ─────────────────────────────────────────────────────────────


async def sweep(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    queued: list[str] = []
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        for store_id in await repo.live_store_ids(db):
            store = await db.get(StoreModel, store_id)
            tz = resolve_store_timezone_name(store.settings)
            if not svc.in_window(now, tz):
                continue
            room = (await repo.settings(db, store_id))["daily_cap"] - (
                await repo.sent_today(db, store, now)
            )
            if room > 0:
                ids = await repo.due(db, store_id, now, min(room, BATCH))
                await repo.lease(db, ids, now)
                queued += [str(i) for i in ids]
        await db.commit()
    for request_id in queued:
        send_task.apply_async(args=[request_id])
    return {"queued": len(queued)}


@celery_app.task(name="apps.reviews.sweep")
def sweep_task() -> dict:
    return _run_async(sweep())


# ─── send ──────────────────────────────────────────────────────────────

#: Everything that ends a request as ``skipped`` rather than ``failed``.
_SKIPPED = _STOP | {"not_installed", "switch_off", "late", "order_closed", "reviewed"}


async def _skip_reason(
    db: Any, request: Request, order: Any, now: datetime
) -> str | None:
    """Why a due request must not go out now, if it must not."""
    if not await is_live(db, request.store_id, svc.SLUG):
        return "not_installed"
    if not (await repo.settings(db, request.store_id))["requests_enabled"]:
        return "switch_off"
    if now - svc.aware(request.due_at) > svc.SEND_LATE:
        return "late"
    if order is None or str(order.status) != "delivered":
        return "order_closed"
    items = await repo.order_items(db, order)
    if not items or all(i["reviewed"] for i in items):
        return "reviewed"
    return None


async def send_whatsapp(
    db: Any, store: Any, order: Any, token: str
) -> tuple[str | None, str | None]:
    """``(provider message id, None)`` or ``(None, reason)``. Through core:
    the store's transport and paid access, the send guard and the
    ``message_logs`` row that counts it against the store's allowance."""
    # ponytail: the same steps as order_tracking_tasks.send_whatsapp with
    # another template; fold both into one helper when a third app sends.
    from src.application.services.back_in_stock_whatsapp import whatsapp_state
    from src.core.enums.whatsapp import TemplateCategory
    from src.core.interfaces.services.messaging_service import (
        MessageContent,
        MessageRecipient,
        MessageType,
    )
    from src.core.services.whatsapp_send_guard import GuardContext, check
    from src.infrastructure.events.handlers.whatsapp_notification_handler import (
        _persist_message_log,
    )
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service
    from src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher import (
        _resolve_language,
    )
    from src.infrastructure.repositories.whatsapp_opt_in_repository import (
        WhatsAppOptInRepository,
    )

    template = "app_reviews_request_v1"
    state = await whatsapp_state(db, store, template)
    if not state["access"] or not state["credentials_ok"]:
        return None, "no_access"

    address = order.shipping_address or {}
    first_name, phone = address.get("first_name"), _e164(address.get("phone"))
    if not phone or not first_name:
        customer = (
            await db.execute(
                select(CustomerModel.first_name, CustomerModel.phone).where(
                    CustomerModel.id == order.customer_id
                )
            )
        ).first()
        if customer is not None:
            first_name = first_name or customer.first_name
            phone = phone or _e164(customer.phone)

    wanted = _resolve_language(store.settings or {}, store.default_language)
    approved = [k for k, v in state["template"].items() if v == "APPROVED"]
    lang = wanted if wanted in approved or not approved else approved[0]
    marketing = state["template_category"].get(lang) == TemplateCategory.MARKETING.value
    optins = WhatsAppOptInRepository(db)
    decision = check(
        GuardContext(
            phone=phone,
            template_name=template,
            template_category=(
                TemplateCategory.MARKETING if marketing else TemplateCategory.UTILITY
            ),
            template_status=state["template"].get(lang),
            store_has_credentials=True,
            store_credentials_marked_invalid=False,
            notification_setting_enabled=True,  # the app's switch, checked before
            has_active_opt_in=bool(phone and marketing)
            and await optins.get_active(store.id, phone) is not None,
            has_opt_out=bool(phone) and await optins.has_opt_out(store.id, phone),
            window_is_open=True,
            already_sent=False,  # the request row is the dedupe
            requires_template_approval=state["needs_approved_template"],
        )
    )
    if not decision.allowed:
        return None, str(getattr(decision.reason, "value", decision.reason))

    service = await get_whatsapp_service(store.id, db, store.tenant_id)
    result = await service.send_message(
        MessageContent(
            type=MessageType.REVIEW_REQUEST,
            recipient=MessageRecipient(phone=phone, language=lang),
            template_params=svc.template_params(
                first_name,
                order.order_number,
                store.name,
                svc.button_value(store.subdomain, token),
                lang,
            ),
        )
    )
    await _persist_message_log(
        db,
        tenant_id=store.tenant_id,
        store_id=store.id,
        phone=phone,
        template_name=template,
        message_id=result.message_id,
        status_str="sent" if result.success else "failed",
        metadata={"app": svc.SLUG, "order_id": str(order.id)},
        error_code=result.error_code,
    )
    if result.success:
        return result.message_id or "sent", None
    return None, (result.error_code or "send_failed")[:64]


async def send(request_id: UUID, *, final_try: bool = True) -> dict:
    """Send one due request. A refusal of the device caps or a provider error
    raises ``SendLater`` (Celery retries) until ``final_try``."""
    async with _sending(request_id) as mine:
        if not mine:
            return {"skipped": "sending"}
        return await _send(request_id, final_try=final_try)


async def _send(request_id: UUID, *, final_try: bool) -> dict:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        request = await db.get(Request, request_id)
        if request is None or request.status != svc.SCHEDULED:
            return {"skipped": "not_scheduled"}
        now = datetime.now(UTC)
        order = await db.get(OrderModel, request.order_id)
        provider_id, reason = None, await _skip_reason(db, request, order, now)
        token = svc.new_token()
        if reason is None:
            store = await db.get(StoreModel, request.store_id)
            # Marked sent before WhatsApp is asked: if anything fails after it
            # accepted the message, no sweep finds the request to send again.
            # At most once: a crash in between loses this one message.
            request.status, request.sent_at = svc.SENT, now
            request.token_hash, request.updated_at = svc.token_hash(token), now
            await db.commit()
            await enable_rls_bypass(db)
            provider_id, reason = await send_whatsapp(db, store, order, token)
            if reason and reason not in _STOP and not final_try:
                countdown = _LATER.get(reason, _RETRY_SECONDS)
                # Back to scheduled; the retry keeps the lease, so no sweep
                # queues it a second time meanwhile.
                await enable_rls_bypass(db)
                request.status, request.sent_at, request.token_hash = (
                    svc.SCHEDULED,
                    None,
                    None,
                )
                request.updated_at = datetime.now(UTC)
                await db.commit()
                raise SendLater(reason, countdown + random.randint(0, 30))
        # The bypass is transaction-local, and core's message log commits.
        await enable_rls_bypass(db)
        request.updated_at = datetime.now(UTC)
        if provider_id:
            request.provider_message_id = provider_id
        else:
            request.status = svc.SKIPPED if reason in _SKIPPED else svc.FAILED
            request.skip_reason = (reason or "send_failed")[:64]
            request.sent_at = request.token_hash = None
        await db.commit()
        logger.info(
            "reviews_request",
            store_id=str(request.store_id),
            status=request.status,
            reason=request.skip_reason,
        )
        return {"status": request.status, "reason": request.skip_reason}


@celery_app.task(
    name="apps.reviews.send", bind=True, max_retries=6, default_retry_delay=120
)
def send_task(self, request_id: str) -> dict:
    try:
        return _run_async(
            send(UUID(request_id), final_try=self.request.retries >= self.max_retries)
        )
    except SendLater as exc:
        raise self.retry(exc=exc, countdown=exc.countdown) from exc

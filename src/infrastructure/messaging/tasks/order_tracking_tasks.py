"""Order Tracking app: the reconcile, the message sweep and one message's send.

Every entry point checks the install gate first, so a store that uninstalled
(or never installed) does nothing; a message queued before an uninstall is
skipped when it comes due.

- ``reconcile`` (daily, and once per store when its settings row is made):
  parcel rows for the window's shipped orders that have none (bulk updates,
  the first install), and retention.
- ``sweep`` (every 10 minutes): a shipment that moved into out-for-delivery
  or failed in the last two hours without a message row gets one (carrier
  polls and bulk updates whose handlers never ran); a scheduled message whose
  Celery message was lost is queued again. Older changes are never messaged.
- ``send``: one WhatsApp message, through core's WhatsApp messaging service
  (never ``whatsapp/scheduled-sends``).
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.application.services import order_tracking_service as svc
from src.application.services.app_install_gate import is_live
from src.application.services.back_in_stock import normalize_phone
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingMessageModel as Message,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.messaging.tasks.back_in_stock_app_tasks import _sending
from src.infrastructure.repositories import order_tracking_repository as repo
from src.infrastructure.tenancy.rls import enable_rls_bypass

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None

#: A scheduled message this long past its due time lost its send task.
LOST_AFTER = timedelta(minutes=15)
#: The reconcile commits this often, so the first-run screen shows progress.
BATCH = 50


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


# ─── reconcile ─────────────────────────────────────────────────────────


async def reconcile(store_id: UUID | None = None, now: datetime | None = None) -> dict:
    """Parcel rows for shipped orders without one; with no store, every live
    store plus retention."""
    now = now or datetime.now(UTC)
    tracked = 0
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if store_id is None:
            stores = await repo.live_store_ids(db)
        else:
            stores = [store_id] if await is_live(db, store_id, svc.SLUG) else []
        for sid in stores:
            for order_id in await repo.untracked(db, sid, now - svc.WINDOW):
                await repo.track(db, order_id)
                tracked += 1
                if tracked % BATCH == 0:
                    await db.commit()
                    await enable_rls_bypass(db)  # transaction-local
        expired = await repo.expire(db, now) if store_id is None else {}
        await db.commit()
    return {"tracked": tracked, **expired}


@celery_app.task(
    name="apps.order_tracking.reconcile",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def reconcile_task(self, store_id: str | None = None) -> dict:
    try:
        return _run_async(reconcile(UUID(store_id) if store_id else None))
    except Exception as exc:  # noqa: BLE001 — tomorrow's run finds it again anyway
        raise self.retry(exc=exc) from exc


# ─── sweep ─────────────────────────────────────────────────────────────


async def sweep(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    due: list[tuple[str, datetime]] = []
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        for store_id in await repo.live_store_ids(db):
            store = await db.get(StoreModel, store_id)
            for row in await repo.recent_changes(db, store_id, now - svc.SEND_LATE):
                at = svc.entered_at(row.status_history, row.status)
                if at is None or at < now - svc.SEND_LATE:
                    continue  # never sent late
                message = await repo.schedule(
                    db,
                    store,
                    row.order_id,
                    svc.KIND_OF_STATUS[row.status],
                    svc.entries(row.status_history, row.status),
                    now,
                )
                if message is not None and message.status == svc.SCHEDULED:
                    due.append((str(message.id), message.send_at))
            for message in await repo.overdue(db, store_id, now - LOST_AFTER):
                due.append((str(message.id), now))
        await db.commit()
    for message_id, eta in due:
        send_task.apply_async(args=[message_id], eta=eta)
    return {"queued": len(due)}


@celery_app.task(name="apps.order_tracking.sweep")
def sweep_task() -> dict:
    return _run_async(sweep())


# ─── send ──────────────────────────────────────────────────────────────

#: Refusals retrying cannot change: the message is skipped.
_STOP = {
    "no_access",
    "no_phone",
    "invalid_phone",
    "opt_out",
    "no_opt_in",
    "template_not_approved",
    "no_credentials",
    "credentials_invalid",
    "merchant_setting_off",
}
#: Everything that ends a message as ``skipped`` rather than ``failed``.
_SKIPPED = _STOP | {"not_installed", "switch_off", "late", "stale"}
#: The GOWA device's caps refuse a send instead of delaying it: try again
#: after the window (seconds), still bounded by the two-hour rule.
_LATER = {"rate_limited_minute": 90, "rate_limited_hour": 600, "warmup_cap": 1800}
_RETRY_SECONDS = 120


class SendLater(Exception):
    def __init__(self, reason: str, countdown: int) -> None:
        super().__init__(reason)
        self.countdown = countdown


def _e164(raw: str | None) -> str | None:
    """An Egyptian mobile in any spelling, or a number already in E.164."""
    value = (raw or "").strip()
    if not value:
        return None
    return normalize_phone(value) or (
        value if re.fullmatch(r"\+\d{8,15}", value) else None
    )


async def _skip_reason(db: Any, message: Message, now: datetime) -> str | None:
    """Why a due message must not go out now, if it must not."""
    if not await is_live(db, message.store_id, svc.SLUG):
        return "not_installed"
    if not (await repo.settings(db, message.store_id))[svc.SWITCH[message.kind]]:
        return "switch_off"
    send_at = svc.aware(message.send_at) or now
    if now - send_at > svc.SEND_LATE:
        return "late"
    status = svc.KINDS[message.kind][0]
    shipment = await repo.latest_shipment(db, message.order_id)
    if (
        shipment is None
        or shipment.status != status
        or svc.entries(shipment.status_history, status) != message.attempt
    ):
        return "stale"  # the parcel moved on since
    return None


async def send_whatsapp(
    db: Any, store: Any, order: Any, message: Message
) -> tuple[str | None, str | None]:
    """Send one message: ``(provider message id, None)`` or ``(None, reason)``.

    Through core: the store's transport and paid access, the send guard
    (opt-out, approval) and the ``message_logs`` row that counts it against
    the store's allowance.
    """
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

    template = svc.KINDS[message.kind][1]
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

    # The store's message language when its template is approved, else one
    # that is (App 01's rule): a message in the other language beats none.
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
            # Read only when Meta filed the template as marketing: the guard
            # asks for an opt-in for nothing else.
            has_active_opt_in=bool(phone and marketing)
            and await optins.get_active(store.id, phone) is not None,
            has_opt_out=bool(phone) and await optins.has_opt_out(store.id, phone),
            window_is_open=True,
            already_sent=False,  # the message row is the dedupe
            requires_template_approval=state["needs_approved_template"],
        )
    )
    if not decision.allowed:
        return None, str(getattr(decision.reason, "value", decision.reason))

    service = await get_whatsapp_service(store.id, db, store.tenant_id)
    result = await service.send_message(
        MessageContent(
            type=MessageType(message.kind),
            recipient=MessageRecipient(phone=phone, language=lang),
            template_params=svc.template_params(
                first_name,
                order.order_number,
                store.name,
                svc.button_value(store.subdomain, order.id),
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
        metadata={
            "app": svc.SLUG,
            "order_id": str(order.id),
            "kind": message.kind,
            "attempt": message.attempt,
        },
        error_code=result.error_code,
    )
    if result.success:
        return result.message_id or "sent", None
    return None, (result.error_code or "send_failed")[:64]


async def send(message_id: UUID, *, final_try: bool = True) -> dict:
    """Send one due message. A refusal of the device caps or a provider error
    raises ``SendLater`` (Celery retries) until ``final_try``."""
    async with _sending(message_id) as mine:
        if not mine:
            return {"skipped": "sending"}
        return await _send(message_id, final_try=final_try)


async def _send(message_id: UUID, *, final_try: bool) -> dict:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        message = await db.get(Message, message_id)
        if message is None or message.status != svc.SCHEDULED:
            return {"skipped": "not_scheduled"}
        now = datetime.now(UTC)
        provider_id, reason = None, await _skip_reason(db, message, now)
        if reason is None:
            store = await db.get(StoreModel, message.store_id)
            order = await repo.order(db, message.order_id)
            provider_id, reason = await send_whatsapp(db, store, order, message)
            if reason and reason not in _STOP and not final_try:
                countdown = _LATER.get(reason, _RETRY_SECONDS)
                raise SendLater(reason, countdown + random.randint(0, 30))
        # The bypass is transaction-local, and core's message log commits.
        await enable_rls_bypass(db)
        message.updated_at = datetime.now(UTC)
        if provider_id:
            message.status, message.provider_message_id = svc.SENT, provider_id
        else:
            message.status = svc.SKIPPED if reason in _SKIPPED else svc.FAILED
            message.skip_reason = (reason or "send_failed")[:64]
        await db.commit()
        logger.info(
            "order_tracking_message",
            store_id=str(message.store_id),
            kind=message.kind,
            attempt=message.attempt,
            status=message.status,
            reason=message.skip_reason,
        )
        return {"status": message.status, "reason": message.skip_reason}


@celery_app.task(
    name="apps.order_tracking.send", bind=True, max_retries=6, default_retry_delay=120
)
def send_task(self, message_id: str) -> dict:
    try:
        return _run_async(
            send(UUID(message_id), final_try=self.request.retries >= self.max_retries)
        )
    except SendLater as exc:
        raise self.retry(exc=exc, countdown=exc.countdown) from exc

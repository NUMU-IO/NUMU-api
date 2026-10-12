"""Back in Stock app on WhatsApp: the store's state, and one alert's send.

Everything goes through core: the transport (``get_whatsapp_service``: the
store's own Meta account, NUMU's shared number, or GOWA as text), the paid
access (``entitlement``), the send guard and the ``message_logs`` audit row,
which counts the message against the store's allowance like core's sends.

BIS-D8: the shopper asked for exactly this alert, so their sign-up is the
consent the guard's marketing rule asks for, if Meta files the template as
marketing. An explicit opt-out still wins.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services import back_in_stock as bis
from src.application.services.whatsapp_entitlement import entitlement
from src.core.enums.whatsapp import TemplateCategory
from src.core.interfaces.services.messaging_service import (
    MessageContent,
    MessageRecipient,
    MessageType,
)
from src.core.services.whatsapp_send_guard import GuardContext, check
from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from src.infrastructure.external_services.whatsapp import (
    get_whatsapp_service,
    requires_template_approval,
    resolve_provider_name,
)
from src.infrastructure.repositories.whatsapp_opt_in_repository import (
    WhatsAppOptInRepository,
)


def _value(v: Any) -> str:
    return str(getattr(v, "value", v))


async def whatsapp_state(db: AsyncSession, store: Any) -> dict[str, Any]:
    """What the merchant's status screen shows, read in process."""
    settings = store.settings or {}
    cred = (
        (
            await db.execute(
                select(ServiceCredential).where(
                    ServiceCredential.tenant_id == store.tenant_id,
                    ServiceCredential.service_type == ServiceType.WHATSAPP,
                    ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
                    ServiceCredential.is_active.is_(True),
                )
            )
        )
        .scalars()
        .first()
    )
    access = await entitlement(db, store.id)
    rows = (
        await db.execute(
            select(
                WhatsAppTemplateModel.language,
                WhatsAppTemplateModel.status,
                WhatsAppTemplateModel.category,
            ).where(
                WhatsAppTemplateModel.store_id == store.id,
                WhatsAppTemplateModel.name == bis.TEMPLATE_NAME,
            )
        )
    ).all()
    return {
        "mode": "own" if cred is not None and cred.extra_metadata else "shared",
        "transport": "gowa" if resolve_provider_name(settings) == "gowa" else "meta",
        "access": access.active,
        "access_reason": access.reason,
        "credentials_ok": not (settings.get("whatsapp") or {}).get("credential_error"),
        "needs_approved_template": requires_template_approval(settings),
        "template": {r.language: _value(r.status) for r in rows},
        "template_category": {r.language: _value(r.category) for r in rows},
    }


def can_send(state: dict[str, Any]) -> bool:
    """Can an alert go out on WhatsApp now (else the app uses email)."""
    if not state["access"] or not state["credentials_ok"]:
        return False
    if not state["needs_approved_template"]:  # GOWA sends text
        return True
    return "APPROVED" in state["template"].values()


async def whatsapp_usable(db: AsyncSession, store: Any) -> bool:
    return can_send(await whatsapp_state(db, store))


async def send_alert(
    db: AsyncSession, store: Any, waiter: Any
) -> tuple[str | None, str | None]:
    """Send one alert: ``(message_id, None)`` or ``(None, fail_reason)``."""
    from src.infrastructure.events.handlers.whatsapp_notification_handler import (
        _persist_message_log,
    )

    state = await whatsapp_state(db, store)
    if not state["access"] or not state["credentials_ok"]:
        return None, "whatsapp_not_connected"
    # The shopper's language when its template is approved, else one that is:
    # can_send() needs only one, and an alert in the other language beats a
    # waiter failed for good.
    wanted = "en" if waiter.locale == "en" else "ar"
    approved = [k for k, v in state["template"].items() if v == "APPROVED"]
    lang = wanted if wanted in approved or not approved else approved[0]
    template_status = state["template"].get(lang) or next(
        iter(state["template"].values()), None
    )
    category = state["template_category"].get(lang)
    decision = check(
        GuardContext(
            phone=waiter.contact,
            template_name=bis.TEMPLATE_NAME,
            template_category=(
                TemplateCategory.MARKETING
                if category == TemplateCategory.MARKETING.value
                else TemplateCategory.UTILITY
            ),
            template_status=template_status,
            store_has_credentials=True,
            store_credentials_marked_invalid=False,
            notification_setting_enabled=True,
            has_active_opt_in=True,  # BIS-D8: the sign-up asked for this alert
            has_opt_out=await WhatsAppOptInRepository(db).has_opt_out(
                store.id, waiter.contact
            ),
            window_is_open=True,
            already_sent=False,
            requires_template_approval=state["needs_approved_template"],
        )
    )
    if not decision.allowed:
        return None, _value(decision.reason)

    service = await get_whatsapp_service(store.id, db, store.tenant_id)
    result = await service.send_message(
        MessageContent(
            type=MessageType.BACK_IN_STOCK_ALERT,
            recipient=MessageRecipient(phone=waiter.contact, language=lang),
            template_params=bis.template_params(store.name, store.subdomain, waiter),
        )
    )
    await _persist_message_log(
        db,
        tenant_id=store.tenant_id,
        store_id=store.id,
        phone=waiter.contact,
        template_name=bis.TEMPLATE_NAME,
        message_id=result.message_id,
        status_str="sent" if result.success else "failed",
        metadata={"app": bis.SLUG, "waiter_id": str(waiter.id)},
        error_code=result.error_code,
    )
    if result.success:
        return result.message_id or "sent", None
    return None, (result.error_code or "send_failed")[:64]

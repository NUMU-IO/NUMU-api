"""Celery tasks — merchant wallet notifications (pay-as-you-go tier).

Email-only in v1: WhatsApp business-initiated messages require an approved
Meta template, and no wallet template exists yet. When one is approved, add
a WhatsApp send next to the email (platform WABA, template gated the same
way COD Autopilot templates are). The hub LowBalanceBanner reads the level
straight from ``GET /wallet``, so in-product warning needs no push.

Enqueued by the commission handler / top-up credit paths only when the
warning ladder RISES (deduped via ``merchant_wallets.last_warning_level``).
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_LEVEL_COPY = {
    1: (
        "Your NUMU wallet balance is low",
        "رصيد محفظتك في نومو منخفض",
        "Your prepaid wallet balance is running low. Top up now to keep "
        "your store running without interruption.",
        "رصيد محفظتك المدفوعة مقدماً أوشك على النفاد. اشحن رصيدك الآن "
        "لتجنب أي انقطاع في متجرك.",
    ),
    2: (
        "Your NUMU wallet balance is negative",
        "رصيد محفظتك في نومو بالسالب",
        "Your wallet balance has gone below zero. Please top up soon — "
        "checkout will be paused if the balance falls further.",
        "أصبح رصيد محفظتك أقل من الصفر. يرجى شحن الرصيد قريباً — سيتم "
        "إيقاف إتمام الطلبات مؤقتاً إذا انخفض الرصيد أكثر.",
    ),
    3: (
        "Checkout paused — top up your NUMU wallet",
        "تم إيقاف إتمام الطلبات — اشحن محفظتك في نومو",
        "Your wallet balance is below the allowed limit and checkout on "
        "your storefront is paused. Top up now to resume taking orders "
        "immediately.",
        "رصيد محفظتك أقل من الحد المسموح وتم إيقاف إتمام الطلبات في "
        "متجرك مؤقتاً. اشحن رصيدك الآن لاستئناف استقبال الطلبات فوراً.",
    ),
}


@celery_app.task(name="tasks.send_wallet_warning", bind=True, max_retries=2)
def send_wallet_warning_task(
    self,
    tenant_id: str,
    level: int,
    balance_cents: int | None = None,
) -> dict:
    """Email the tenant owner about a wallet warning-level rise."""
    try:
        return asyncio.run(_send_warning(tenant_id, level, balance_cents))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "wallet_warning_task_failed",
            extra={"tenant_id": tenant_id, "level": level, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=60) from exc


@celery_app.task(name="tasks.send_wallet_topup_credited", bind=True, max_retries=2)
def send_wallet_topup_credited_task(
    self,
    tenant_id: str,
    amount_cents: int,
    balance_cents: int,
) -> dict:
    """Email the tenant owner that their top-up landed."""
    try:
        return asyncio.run(_send_topup_credited(tenant_id, amount_cents, balance_cents))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "wallet_topup_credited_task_failed",
            extra={"tenant_id": tenant_id, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=60) from exc


async def _owner_email(session, tenant_id: str) -> str | None:
    from sqlalchemy import select

    from src.infrastructure.database.models.public.tenant import TenantModel
    from src.infrastructure.database.models.public.user import UserModel

    row = (
        await session.execute(
            select(UserModel.email)
            .join(TenantModel, TenantModel.owner_id == UserModel.id)
            .where(TenantModel.id == tenant_id)
        )
    ).scalar_one_or_none()
    return str(row) if row else None


def _egp(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"{cents / 100:,.2f} EGP"


async def _send_warning(tenant_id: str, level: int, balance_cents: int | None) -> dict:
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    copy = _LEVEL_COPY.get(level)
    if copy is None:
        return {"status": "skipped", "reason": "unknown_level"}
    subject_en, subject_ar, body_en, body_ar = copy

    async with AsyncSessionLocal() as session:
        email = await _owner_email(session, tenant_id)
    if not email:
        return {"status": "skipped", "reason": "no_owner_email"}

    html = (
        f"<div dir='rtl' style='font-family:sans-serif'><p>{body_ar}</p>"
        f"<p>الرصيد الحالي: {_egp(balance_cents)}</p></div><hr>"
        f"<div style='font-family:sans-serif'><p>{body_en}</p>"
        f"<p>Current balance: {_egp(balance_cents)}</p></div>"
    )
    sent = await ResendEmailService().send_email(
        EmailMessage(
            to=email,
            subject=f"{subject_ar} | {subject_en}",
            html_content=html,
        )
    )
    return {"status": "sent" if sent else "failed", "level": level}


async def _send_topup_credited(
    tenant_id: str, amount_cents: int, balance_cents: int
) -> dict:
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    async with AsyncSessionLocal() as session:
        email = await _owner_email(session, tenant_id)
    if not email:
        return {"status": "skipped", "reason": "no_owner_email"}

    html = (
        f"<div dir='rtl' style='font-family:sans-serif'>"
        f"<p>تم شحن محفظتك بمبلغ {_egp(amount_cents)} بنجاح.</p>"
        f"<p>الرصيد الحالي: {_egp(balance_cents)}</p></div><hr>"
        f"<div style='font-family:sans-serif'>"
        f"<p>Your wallet was topped up with {_egp(amount_cents)}.</p>"
        f"<p>Current balance: {_egp(balance_cents)}</p></div>"
    )
    sent = await ResendEmailService().send_email(
        EmailMessage(
            to=email,
            subject="تم شحن محفظتك | Wallet topped up",
            html_content=html,
        )
    )
    return {"status": "sent" if sent else "failed"}

"""Active merchant notification when the trust auto-approve kill-switch fires (P1-3).

The daily evaluator (``trust_kill_switch_tasks``) flips
``auto_approve_on_trust_enabled`` off when an auto-approved cohort's RTO rate
breaches the safety threshold, and persists a banner via
``auto_disabled_reason``. That banner is passive — a merchant who doesn't open
the app won't know their automation stopped. This handler emails the store
owner so the disable is never silent.

Best-effort: any missing piece (store, owner email) logs a structured skip and
returns; a notification failure must never affect the evaluator.
"""

from __future__ import annotations

from sqlalchemy import select, text

from src.core.events.risk_events import TrustKillSwitchFiredEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal

logger = get_logger(__name__)


async def handle_trust_kill_switch_fired(event: TrustKillSwitchFiredEvent) -> None:
    """Email the store owner that trust-based auto-approve was turned off."""
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.models.public.user import UserModel
    from src.infrastructure.database.models.tenant.store import StoreModel

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        store: StoreModel | None = (
            await session.execute(
                select(StoreModel).where(StoreModel.id == event.store_id)
            )
        ).scalar_one_or_none()
        if store is None:
            logger.warning(
                "kill_switch_email_skipped",
                store_id=str(event.store_id),
                reason="store_not_found",
            )
            return

        owner: UserModel | None = (
            await session.execute(
                select(UserModel).where(UserModel.id == store.owner_id)
            )
        ).scalar_one_or_none()
        recipient = (owner.email if owner else None) or getattr(
            store, "contact_email", None
        )
        store_name = store.name
        language = (
            "en" if (store.default_language or "ar").lower().startswith("en") else "ar"
        )

    if not recipient:
        logger.warning(
            "kill_switch_email_skipped",
            store_id=str(event.store_id),
            reason="no_merchant_email",
        )
        return

    subject, html = _compose(language, store_name, event)

    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    try:
        sent = await ResendEmailService().send_email(
            EmailMessage(to=recipient, subject=subject, html_content=html)
        )
    except Exception:
        logger.exception("kill_switch_email_failed", store_id=str(event.store_id))
        return

    logger.info(
        "kill_switch_email_sent",
        store_id=str(event.store_id),
        email=recipient,
        success=sent,
    )


def _compose(
    language: str, store_name: str, event: TrustKillSwitchFiredEvent
) -> tuple[str, str]:
    """Return (subject, html_content) in the merchant's language."""
    if language == "ar":
        subject = "تم إيقاف الموافقة التلقائية المبنية على الثقة"
        html = (
            f"<p>مرحباً {store_name}،</p>"
            f"<p>قمنا مؤقتاً بإيقاف الموافقة التلقائية على طلبات الدفع عند الاستلام "
            f"للعملاء الموثوقين كإجراء وقائي.</p>"
            f"<p>{event.reason}</p>"
            f"<p>يمكنك مراجعة حد الثقة وإعادة التفعيل من إعدادات التطبيق في أي وقت.</p>"
        )
        return subject, html

    subject = "Trust-based auto-approve was turned off"
    html = (
        f"<p>Hi {store_name},</p>"
        f"<p>We temporarily turned off trust-based auto-approval of COD orders "
        f"as a safety measure.</p>"
        f"<p>{event.reason}</p>"
        f"<p>You can review your trust threshold and re-enable it from the app "
        f"settings whenever you're ready.</p>"
    )
    return subject, html

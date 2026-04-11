"""Celery task: nudge merchants who abandoned onboarding.

Stream 7.1 of the NUMU plan. Runs every 6 hours. Finds tenants where
onboarding is incomplete and last action was >24h ago, sends a
WhatsApp/email nudge tied to the next incomplete step.

Skips demo tenants (they have seeded data and don't need nudges).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

# Step-specific nudge messages (Arabic)
STEP_NUDGES_AR = {
    "ADD_PRODUCT": "محتاج مساعدة في إضافة أول منتج؟ 📦",
    "SET_IDENTITY": "ظبط هوية متجرك — اللوجو والألوان بتفرق! 🎨",
    "CONFIRM_SUPPORT": "ضيف قنوات الدعم عشان عملائك يوصلوك بسهولة 📞",
    "CONFIGURE_PAYMENT": "اظبط الدفع عشان تقدر تستلم فلوسك 💳",
    "ADD_SHIPPING": "ضيف طريقة الشحن — بوسطة جاهزة في دقيقة 🚚",
    "FIRST_ORDER": "متجرك جاهز! شارك الرابط واستنى أول أوردر 🎉",
}

STEP_NUDGES_EN = {
    "ADD_PRODUCT": "Need help adding your first product? 📦",
    "SET_IDENTITY": "Set up your store identity — logo and colors matter! 🎨",
    "CONFIRM_SUPPORT": "Add support channels so customers can reach you 📞",
    "CONFIGURE_PAYMENT": "Set up payments to start collecting revenue 💳",
    "ADD_SHIPPING": "Add a shipping method — Bosta is ready in a minute 🚚",
    "FIRST_ORDER": "Your store is ready! Share the link and wait for your first order 🎉",
}


def _run_nudges(batch_size: int) -> dict:
    return asyncio.run(_async_nudges(batch_size))


async def _async_nudges(batch_size: int) -> dict:
    from sqlalchemy import select

    from src.infrastructure.database.connection import (
        AsyncSessionLocal as session_factory,
    )
    from src.infrastructure.database.models import StoreModel
    from src.infrastructure.database.models.public.onboarding import (
        StoreOnboardingModel,
    )
    from src.infrastructure.database.models.public.tenant import TenantModel

    sent = 0
    skipped = 0
    cutoff = datetime.now(UTC) - timedelta(hours=24)

    async with session_factory() as session:
        # Find incomplete onboardings where last update was >24h ago
        q = (
            select(StoreOnboardingModel)
            .where(StoreOnboardingModel.is_completed.is_(False))
            .where(StoreOnboardingModel.is_dismissed.is_(False))
            .where(StoreOnboardingModel.updated_at < cutoff)
            .limit(batch_size)
        )
        onboardings = (await session.execute(q)).scalars().all()

        for ob in onboardings:
            try:
                # Get the store and tenant
                store = (
                    await session.execute(
                        select(StoreModel).where(StoreModel.id == ob.store_id)
                    )
                ).scalar_one_or_none()
                if not store:
                    skipped += 1
                    continue

                tenant = (
                    await session.execute(
                        select(TenantModel).where(TenantModel.id == store.tenant_id)
                    )
                ).scalar_one_or_none()
                if not tenant or tenant.is_demo:
                    skipped += 1
                    continue

                # Find next incomplete step
                steps = ob.steps or {}
                next_step = None
                for step_key in [
                    "ADD_PRODUCT",
                    "SET_IDENTITY",
                    "CONFIRM_SUPPORT",
                    "CONFIGURE_PAYMENT",
                    "ADD_SHIPPING",
                    "FIRST_ORDER",
                ]:
                    step_data = steps.get(step_key, {})
                    if not step_data.get("completed_at") and not step_data.get(
                        "skipped_at"
                    ):
                        next_step = step_key
                        break

                if not next_step:
                    skipped += 1
                    continue

                lang = store.default_language or "ar"
                nudge_msg = (STEP_NUDGES_AR if lang == "ar" else STEP_NUDGES_EN).get(
                    next_step, ""
                )

                # Send via WhatsApp if owner has a phone
                owner_phone = None
                if tenant.owner_id:
                    from src.infrastructure.database.models import UserModel

                    owner = (
                        await session.execute(
                            select(UserModel).where(UserModel.id == tenant.owner_id)
                        )
                    ).scalar_one_or_none()
                    if owner and owner.phone:
                        owner_phone = owner.phone

                if owner_phone and nudge_msg:
                    try:
                        from src.infrastructure.external_services.whatsapp.messaging_service import (
                            WhatsAppMessagingService,
                        )

                        wa = WhatsAppMessagingService()
                        await wa.send_text_message(owner_phone, nudge_msg)
                        sent += 1
                    except Exception:
                        logger.warning(
                            "onboarding_nudge_whatsapp_failed", exc_info=True
                        )
                        skipped += 1
                else:
                    skipped += 1

            except Exception:
                logger.warning("onboarding_nudge_error", exc_info=True)
                skipped += 1

    return {"sent": sent, "skipped": skipped}


@celery_app.task(
    name="tasks.send_onboarding_nudges",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
    acks_late=True,
)
def send_onboarding_nudges(self, batch_size: int = 50) -> dict:
    """Nudge merchants who abandoned onboarding >24h ago."""
    try:
        logger.info("Starting onboarding nudge sweep …")
        result = _run_nudges(batch_size)
        logger.info("Onboarding nudge sweep complete: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("Onboarding nudge sweep failed")
        raise self.retry(exc=exc)

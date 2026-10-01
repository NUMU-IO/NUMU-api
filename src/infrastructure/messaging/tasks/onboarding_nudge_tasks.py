"""Celery tasks for scheduled onboarding nudge emails.

These run on a daily beat schedule to catch merchants who:
1. Created a store but haven't added a product of their own (24h / 3d)
2. Have trials expiring soon (7d / 3d / 1d warnings)

Each email is sent at most once, tracked via a Redis key:
``email_sent:{id}:{event_key}`` (store id for the product nudges, user id
for the trial warnings).
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


async def _was_sent(redis, user_id: str, event_key: str) -> bool:
    """Check if an email was already sent for this user+event."""
    return await redis.exists(f"email_sent:{user_id}:{event_key}")


async def _mark_sent(
    redis, user_id: str, event_key: str, ttl_days: int | None = 90
) -> None:
    """Mark an email as sent (expires after ttl_days; None = never)."""
    await redis.set(
        f"email_sent:{user_id}:{event_key}",
        "1",
        ex=ttl_days * 86400 if ttl_days else None,
    )


@celery_app.task(
    name="tasks.send_inactive_merchant_nudges",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def send_inactive_merchant_nudges(self):
    """Nudge owners of stores 1-14 days old that still have no product of
    their own (sample products don't count). Once per store, in the store's
    language. Runs daily via Celery Beat.
    """
    from sqlalchemy import exists, select

    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.user import UserModel
    from src.infrastructure.database.models.tenant.product import ProductModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.messaging.redis_client import get_redis_client

    async def _process():
        redis = await get_redis_client()
        async with AsyncSessionLocal() as session:
            now = datetime.now(UTC)

            cutoff_24h = now - timedelta(hours=24)
            cutoff_3d = now - timedelta(days=3)
            # New stores only: this is an onboarding nudge, not a reminder
            # every merchant gets forever.
            oldest = now - timedelta(days=14)
            has_real_product = exists().where(
                ProductModel.store_id == StoreModel.id,
                ~ProductModel.slug.startswith("demo-"),
            )

            result = await session.execute(
                select(UserModel, StoreModel)
                .join(StoreModel, StoreModel.owner_id == UserModel.id)
                .where(
                    UserModel.status == "ACTIVE",
                    UserModel.email_verified_at.is_not(None),
                    StoreModel.created_at <= cutoff_24h,
                    StoreModel.created_at >= oldest,
                    ~has_real_product,
                )
            )

            merchants = result.all()
            service = ResendEmailService()
            sent_count = 0

            for user, store in merchants:
                store_id = str(store.id)
                language = "en" if store.default_language == "en" else "ar"
                event_key = (
                    "inactive_3d" if store.created_at <= cutoff_3d else "inactive_24h"
                )
                if await _was_sent(redis, store_id, event_key):
                    continue
                trial_days = (
                    max(
                        0, math.ceil((user.trial_ends_at - now).total_seconds() / 86400)
                    )
                    if event_key == "inactive_3d" and user.trial_ends_at
                    else None
                )
                subject, html = _inactive_email(
                    language,
                    name=user.first_name,
                    store_name=store.name,
                    later=event_key == "inactive_3d",
                    trial_days_left=trial_days,
                )
                try:
                    await service.send_email(
                        EmailMessage(to=user.email, subject=subject, html_content=html)
                    )
                    await _mark_sent(redis, store_id, event_key, ttl_days=None)
                    sent_count += 1
                except Exception:
                    logger.warning(
                        "inactive_nudge_email_failed",
                        store_id=store_id,
                        event_key=event_key,
                    )

            logger.info("inactive_nudges_complete", sent=sent_count)
            return {"sent": sent_count}

    try:
        return _run_async(_process())
    except Exception as e:
        logger.error("inactive_nudges_error", error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_trial_expiry_warnings",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def send_trial_expiry_warnings(self):
    """Find merchants with trials expiring in 7d/3d/1d and send warning emails.

    Runs daily via Celery Beat.
    """
    from sqlalchemy import select

    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.user import UserModel
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.messaging.redis_client import get_redis_client

    async def _process():
        redis = await get_redis_client()
        async with AsyncSessionLocal() as session:
            now = datetime.now(UTC)

            result = await session.execute(
                select(UserModel).where(
                    UserModel.trial_ends_at.is_not(None),
                    UserModel.trial_ends_at > now,
                    UserModel.status == "ACTIVE",
                )
            )

            users = result.scalars().all()
            service = ResendEmailService()
            sent_count = 0

            thresholds = [
                (7, "trial_7d", "7 days left on your trial"),
                (3, "trial_3d", "3 days left — upgrade now"),
                (1, "trial_1d", "Last day of your trial"),
            ]

            for user in users:
                days_left = (user.trial_ends_at - now).days
                user_id = str(user.id)

                for threshold_days, event_key, subject in thresholds:
                    if days_left == threshold_days:
                        if not await _was_sent(redis, user_id, event_key):
                            try:
                                await service.send_email(
                                    EmailMessage(
                                        to=user.email,
                                        subject=subject,
                                        html_content=_trial_warning_html(
                                            name=user.first_name,
                                            days_left=threshold_days,
                                        ),
                                    )
                                )
                                await _mark_sent(redis, user_id, event_key)
                                sent_count += 1
                            except Exception:
                                logger.warning(
                                    "trial_warning_email_failed",
                                    user_id=user_id,
                                    # Not ``event=``: structlog's first argument
                                    # is ``event``, so that kwarg raised TypeError.
                                    event_key=event_key,
                                )

            logger.info("trial_warnings_complete", sent=sent_count)
            return {"sent": sent_count}

    try:
        return _run_async(_process())
    except Exception as e:
        logger.error("trial_warnings_error", error=str(e))
        raise self.retry(exc=e)


# ──────────── Email Templates ────────────


_INACTIVE_COPY = {
    "ar": {
        "subject": ("متجرك مستني أول منتج", "متجرك لسه من غير منتجات"),
        "title": ("متجرك مستني أول منتج!", "خلّي متجرك يبدأ يبيع"),
        "greeting": "أهلاً {name}،",
        "greeting_anon": "أهلاً بيك،",
        "body": (
            "متجرك <strong>{store}</strong> جاهز، بس لسه مفيهوش منتج من منتجاتك. "
            "إضافة أول منتج بتاخد دقيقتين، وهي أهم خطوة عشان تبدأ تبيع."
        ),
        "btn": "ضيف أول منتج",
        "trial": "فاضلك {days} يوم في التجربة المجانية.",
        "sign": "فريق نُمو",
    },
    "en": {
        "subject": (
            "Your store is waiting for its first product",
            "Don't lose momentum — your store is waiting",
        ),
        "title": ("Your store is waiting!", "Don't lose momentum!"),
        "greeting": "Hi {name},",
        "greeting_anon": "Hi there,",
        "body": (
            "Your store <strong>{store}</strong> is set up, but it doesn't have "
            "any of your products yet. Adding your first one takes two minutes "
            "and is the most important step to start selling."
        ),
        "btn": "Add your first product",
        "trial": "You have {days} days left on your trial.",
        "sign": "The NUMU Team",
    },
}


def _inactive_email(
    language: str,
    *,
    name: str | None,
    store_name: str,
    later: bool,
    trial_days_left: int | None,
) -> tuple[str, str]:
    """Subject + HTML for the no-product nudge, in the store's language."""
    from html import escape

    c = _INACTIVE_COPY.get(language, _INACTIVE_COPY["ar"])
    i = 1 if later else 0
    rtl = language == "ar"
    greeting = c["greeting"].format(name=escape(name)) if name else c["greeting_anon"]
    trial_line = (
        f'<p style="color: #e67e22; font-weight: 600; margin-top: 16px;">'
        f"{c['trial'].format(days=trial_days_left)}</p>"
        if trial_days_left
        else ""
    )
    html = f"""
    <div dir="{"rtl" if rtl else "ltr"}" style="font-family: Inter, Arial, sans-serif; max-width: 560px; margin: 0 auto; color: #1a1a2e; text-align: {"right" if rtl else "left"};">
        <div style="background: linear-gradient(135deg, #1034A6, #D4AF37); padding: 32px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 22px;">{c["title"][i]}</h1>
        </div>
        <div style="padding: 24px; background: #ffffff; border: 1px solid #e9ecef; border-top: none; border-radius: 0 0 12px 12px;">
            <p>{greeting}</p>
            <p>{c["body"].format(store=escape(store_name))}</p>

            <div style="text-align: center; margin: 24px 0;">
                <a href="https://merchant.numueg.app/products/new?lang={language}"
                   style="display: inline-block; background: #1034A6; color: white;
                          padding: 12px 28px; border-radius: 8px; text-decoration: none;
                          font-weight: 600; font-size: 15px;">
                    {c["btn"]}
                </a>
            </div>

            {trial_line}

            <p style="color: #6c757d; font-size: 13px; margin-top: 30px;">
                &mdash; {c["sign"]}
            </p>
        </div>
    </div>
    """
    return c["subject"][i], html


def _trial_warning_html(name: str | None, days_left: int) -> str:
    greeting = f"Hi {name}," if name else "Hi there,"
    urgency = (
        "This is your last day!"
        if days_left <= 1
        else f"You have {days_left} days left."
    )

    return f"""
    <div style="font-family: Inter, Arial, sans-serif; max-width: 560px; margin: 0 auto; color: #1a1a2e;">
        <div style="background: linear-gradient(135deg, #e67e22, #e74c3c); padding: 32px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 22px;">
                {"Final day of your trial" if days_left <= 1 else f"{days_left} days left on your trial"}
            </h1>
        </div>
        <div style="padding: 24px; background: #ffffff; border: 1px solid #e9ecef; border-top: none; border-radius: 0 0 12px 12px;">
            <p>{greeting}</p>
            <p><strong>{urgency}</strong> Your NUMU trial is coming to an end.
            Upgrade now to keep your store running and unlock all premium features.</p>

            <div style="background: #f8f9fa; border-radius: 8px; padding: 16px; margin: 20px 0;">
                <p style="margin: 0 0 8px; font-weight: 600;">What you'll keep with Premium:</p>
                <ul style="margin: 0; padding-left: 20px; color: #495057; font-size: 14px;">
                    <li>Unlimited products & orders</li>
                    <li>Custom domain support</li>
                    <li>Advanced analytics & health score</li>
                    <li>Priority support</li>
                </ul>
            </div>

            <div style="text-align: center; margin: 24px 0;">
                <a href="https://merchant.numueg.app/settings"
                   style="display: inline-block; background: #e67e22; color: white;
                          padding: 12px 28px; border-radius: 8px; text-decoration: none;
                          font-weight: 600; font-size: 15px;">
                    Upgrade Now
                </a>
            </div>

            <p style="color: #6c757d; font-size: 13px; margin-top: 30px;">
                &mdash; The NUMU Team
            </p>
        </div>
    </div>
    """

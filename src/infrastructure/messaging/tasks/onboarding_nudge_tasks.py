"""Celery tasks for scheduled onboarding nudge emails.

These run on a daily beat schedule to catch merchants who:
1. Signed up but haven't added products (24h / 3d inactive)
2. Have trials expiring soon (7d / 3d / 1d warnings)

Each email type is sent at most once per user, tracked via a
Redis key: ``email_sent:{user_id}:{event_key}``.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from src.config.logging_config import get_logger
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


async def _mark_sent(redis, user_id: str, event_key: str, ttl_days: int = 90):
    """Mark an email as sent (expires after ttl_days)."""
    await redis.set(
        f"email_sent:{user_id}:{event_key}",
        "1",
        ex=ttl_days * 86400,
    )


@celery_app.task(
    name="tasks.send_inactive_merchant_nudges",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def send_inactive_merchant_nudges(self):
    """Find merchants with no products after 24h or 3d and send nudge emails.

    Runs daily via Celery Beat.
    """
    from sqlalchemy import select

    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.user import UserModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.messaging.redis_client import get_redis_client

    async def _process():
        redis = await get_redis_client()
        async with AsyncSessionLocal() as session:
            now = datetime.now(UTC)

            # Find store owners who created their store 24h-3d ago
            # and still have no completed add_product onboarding step
            cutoff_24h = now - timedelta(hours=24)
            cutoff_3d = now - timedelta(days=3)

            result = await session.execute(
                select(UserModel, StoreModel)
                .join(StoreModel, StoreModel.owner_id == UserModel.id)
                .where(
                    UserModel.status == "ACTIVE",
                    UserModel.email_verified_at.is_not(None),
                )
            )

            merchants = result.all()
            service = ResendEmailService()
            sent_count = 0

            for user, store in merchants:
                user_id = str(user.id)
                store_created = store.created_at

                # 3-day nudge
                if store_created and store_created <= cutoff_3d:
                    if not await _was_sent(redis, user_id, "inactive_3d"):
                        trial_days = 0
                        if user.trial_ends_at:
                            trial_days = max(0, (user.trial_ends_at - now).days)
                        try:
                            await service.send_email(
                                EmailMessage(
                                    to=user.email,
                                    subject="Don't lose momentum — your store is waiting",
                                    html_content=_inactive_email_html(
                                        name=user.first_name,
                                        days_inactive=3,
                                        trial_days_left=trial_days,
                                        store_name=store.name,
                                    ),
                                )
                            )
                            await _mark_sent(redis, user_id, "inactive_3d")
                            sent_count += 1
                        except Exception:
                            logger.warning("inactive_3d_email_failed", user_id=user_id)

                # 24-hour nudge
                elif store_created and store_created <= cutoff_24h:
                    if not await _was_sent(redis, user_id, "inactive_24h"):
                        try:
                            await service.send_email(
                                EmailMessage(
                                    to=user.email,
                                    subject="Your store is waiting for products",
                                    html_content=_inactive_email_html(
                                        name=user.first_name,
                                        days_inactive=1,
                                        trial_days_left=None,
                                        store_name=store.name,
                                    ),
                                )
                            )
                            await _mark_sent(redis, user_id, "inactive_24h")
                            sent_count += 1
                        except Exception:
                            logger.warning("inactive_24h_email_failed", user_id=user_id)

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
                                    event=event_key,
                                )

            logger.info("trial_warnings_complete", sent=sent_count)
            return {"sent": sent_count}

    try:
        return _run_async(_process())
    except Exception as e:
        logger.error("trial_warnings_error", error=str(e))
        raise self.retry(exc=e)


# ──────────── Email Templates ────────────


def _inactive_email_html(
    name: str | None,
    days_inactive: int,
    trial_days_left: int | None,
    store_name: str,
) -> str:
    greeting = f"Hi {name}," if name else "Hi there,"
    trial_line = ""
    if trial_days_left is not None and trial_days_left > 0:
        trial_line = f"""
        <p style="color: #e67e22; font-weight: 600; margin-top: 16px;">
            You have {trial_days_left} day{"s" if trial_days_left > 1 else ""} left on your trial.
        </p>
        """

    return f"""
    <div style="font-family: Inter, Arial, sans-serif; max-width: 560px; margin: 0 auto; color: #1a1a2e;">
        <div style="background: linear-gradient(135deg, #1034A6, #D4AF37); padding: 32px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 22px;">
                {"Your store is waiting!" if days_inactive <= 1 else "Don't lose momentum!"}
            </h1>
        </div>
        <div style="padding: 24px; background: #ffffff; border: 1px solid #e9ecef; border-top: none; border-radius: 0 0 12px 12px;">
            <p>{greeting}</p>
            <p>Your store <strong>{store_name}</strong> is set up and ready — but it still
            doesn't have any products. Adding your first product takes just 2 minutes
            and is the most important step to start selling.</p>

            <div style="text-align: center; margin: 24px 0;">
                <a href="https://merchant.numueg.app/products/new"
                   style="display: inline-block; background: #1034A6; color: white;
                          padding: 12px 28px; border-radius: 8px; text-decoration: none;
                          font-weight: 600; font-size: 15px;">
                    Add Your First Product
                </a>
            </div>

            {trial_line}

            <p style="color: #6c757d; font-size: 13px; margin-top: 30px;">
                &mdash; The NUMU Team
            </p>
        </div>
    </div>
    """


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

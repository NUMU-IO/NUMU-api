"""Celery tasks for merchant onboarding email sequence.

Milestones:
1. Welcome email         - sent on merchant registration
2. First product added   - sent when first product is created
3. First order received  - sent when first order arrives (Celery scheduled)
4. Store approved        - sent when admin approves a pending store
"""

import asyncio

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)


def run_async(coro):
    """Run async code in Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="tasks.send_welcome_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_welcome_email_task(
    self,
    email: str,
    merchant_name: str,
    dashboard_url: str = "https://dashboard.numu.io",
    language: str = "en",
):
    """Send welcome email on merchant registration.

    Args:
        email: Merchant email address.
        merchant_name: Merchant first name or business name.
        dashboard_url: URL to the merchant dashboard.
    """
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.onboarding import (
        WELCOME_TEMPLATE,
    )

    try:
        service = ResendEmailService()
        html = WELCOME_TEMPLATE["html_fn"](
            merchant_name, dashboard_url, language=language
        )
        message = EmailMessage(
            to=email,
            subject=WELCOME_TEMPLATE["subject"].get(
                language, WELCOME_TEMPLATE["subject"]["en"]
            ),
            html_content=html,
        )
        result = run_async(service.send_email(message))
        logger.info("welcome_email_sent", email=email, success=result)
        return {"sent": result}
    except Exception as e:
        logger.error("welcome_email_failed", email=email, error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_first_product_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_first_product_email_task(
    self,
    email: str,
    merchant_name: str,
    product_name: str,
    dashboard_url: str = "https://dashboard.numu.io",
    language: str = "en",
):
    """Send congratulations email when merchant adds first product.

    Args:
        email: Merchant email address.
        merchant_name: Merchant display name.
        product_name: Name of the first product added.
        dashboard_url: Dashboard base URL.
    """
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.onboarding import (
        FIRST_PRODUCT_ADDED_TEMPLATE,
    )

    try:
        service = ResendEmailService()
        html = FIRST_PRODUCT_ADDED_TEMPLATE["html_fn"](
            merchant_name, product_name, dashboard_url, language=language
        )
        message = EmailMessage(
            to=email,
            subject=FIRST_PRODUCT_ADDED_TEMPLATE["subject"].get(
                language, FIRST_PRODUCT_ADDED_TEMPLATE["subject"]["en"]
            ),
            html_content=html,
        )
        result = run_async(service.send_email(message))
        logger.info(
            "first_product_email_sent",
            email=email,
            product_name=product_name,
            success=result,
        )
        return {"sent": result}
    except Exception as e:
        logger.error("first_product_email_failed", email=email, error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_first_order_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_first_order_email_task(
    self,
    email: str,
    merchant_name: str,
    order_number: str,
    total: str,
    dashboard_url: str = "https://dashboard.numu.io",
    language: str = "en",
):
    """Send congratulations email when merchant receives first order.

    Args:
        email: Merchant email address.
        merchant_name: Merchant display name.
        order_number: The first order number.
        total: Formatted total amount string.
        dashboard_url: Dashboard base URL.
    """
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.onboarding import (
        FIRST_ORDER_RECEIVED_TEMPLATE,
    )

    try:
        service = ResendEmailService()
        html = FIRST_ORDER_RECEIVED_TEMPLATE["html_fn"](
            merchant_name, order_number, total, dashboard_url, language=language
        )
        message = EmailMessage(
            to=email,
            subject=FIRST_ORDER_RECEIVED_TEMPLATE["subject"].get(
                language, FIRST_ORDER_RECEIVED_TEMPLATE["subject"]["en"]
            ),
            html_content=html,
        )
        result = run_async(service.send_email(message))
        logger.info(
            "first_order_email_sent",
            email=email,
            order_number=order_number,
            success=result,
        )
        return {"sent": result}
    except Exception as e:
        logger.error("first_order_email_failed", email=email, error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    name="tasks.send_store_approved_email",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_store_approved_email_task(self, store_id: str):
    """Send approval email when admin approves a pending store.

    Looks up the store and owner from DB to get email, name, subdomain.

    Args:
        store_id: UUID string of the approved store.
    """
    from sqlalchemy import select

    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import async_session_factory
    from src.infrastructure.database.models.public.user import UserModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.onboarding import (
        STORE_APPROVED_TEMPLATE,
    )

    async def _send():
        async with async_session_factory() as session:
            result = await session.execute(
                select(StoreModel).where(StoreModel.id == store_id)
            )
            store = result.scalar_one_or_none()
            if not store:
                logger.warning(
                    "store_approved_email_skip",
                    reason="store_not_found",
                    store_id=store_id,
                )
                return {"sent": False}

            owner_result = await session.execute(
                select(UserModel).where(UserModel.id == store.owner_id)
            )
            owner = owner_result.scalar_one_or_none()
            if not owner:
                logger.warning(
                    "store_approved_email_skip",
                    reason="owner_not_found",
                    store_id=store_id,
                )
                return {"sent": False}

            language = store.default_language or "en"
            merchant_name = owner.first_name or owner.email
            store_name = store.name
            store_url = (
                f"https://{store.subdomain}.numu.io"
                if store.subdomain
                else f"https://{store.slug}.numu.io"
            )
            dashboard_url = "https://dashboard.numu.io"

            service = ResendEmailService()
            html = STORE_APPROVED_TEMPLATE["html_fn"](
                merchant_name, store_name, store_url, dashboard_url, language=language
            )
            message = EmailMessage(
                to=owner.email,
                subject=STORE_APPROVED_TEMPLATE["subject"].get(
                    language, STORE_APPROVED_TEMPLATE["subject"]["en"]
                ),
                html_content=html,
            )
            sent = await service.send_email(message)
            logger.info(
                "store_approved_email_sent",
                email=owner.email,
                store_id=store_id,
                success=sent,
            )
            return {"sent": sent}

    try:
        return run_async(_send())
    except Exception as e:
        logger.error("store_approved_email_failed", store_id=store_id, error=str(e))
        raise self.retry(exc=e)

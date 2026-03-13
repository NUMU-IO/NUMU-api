"""Repository dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.infrastructure.repositories import (
    CategoryRepository,
    CouponRepository,
    CustomerAddressRepository,
    CustomerRepository,
    FeedbackRepository,
    InvoiceRepository,
    MessageLogRepository,
    OnboardingRepository,
    OrderRepository,
    ProductRepository,
    RefundRepository,
    StoreRepository,
    TwoFactorRepository,
    UserRepository,
    WaitlistRepository,
    WebhookDeliveryLogRepository,
    WebhookSubscriptionRepository,
)


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserRepository:
    """Get user repository dependency."""
    return UserRepository(session)


def get_store_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StoreRepository:
    """Get store repository dependency."""
    return StoreRepository(session)


def get_product_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ProductRepository:
    """Get product repository dependency."""
    return ProductRepository(session)


def get_customer_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CustomerRepository:
    """Get customer repository dependency."""
    return CustomerRepository(session)


def get_customer_address_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CustomerAddressRepository:
    """Get customer address repository dependency."""
    return CustomerAddressRepository(session)


def get_category_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CategoryRepository:
    """Get category repository dependency."""
    return CategoryRepository(session)


def get_coupon_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CouponRepository:
    """Get coupon repository dependency."""
    return CouponRepository(session)


def get_onboarding_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OnboardingRepository:
    """Get onboarding repository dependency."""
    return OnboardingRepository(session)


def get_order_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OrderRepository:
    """Get order repository dependency."""
    return OrderRepository(session)


def get_refund_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> RefundRepository:
    """Get refund repository dependency."""
    return RefundRepository(session)


def get_waitlist_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WaitlistRepository:
    """Get waitlist repository dependency."""
    return WaitlistRepository(session)


def get_feedback_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FeedbackRepository:
    """Get feedback repository dependency."""
    return FeedbackRepository(session)


def get_invoice_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> InvoiceRepository:
    """Get invoice repository dependency."""
    return InvoiceRepository(session)


def get_message_log_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageLogRepository:
    """Get message log repository dependency."""
    return MessageLogRepository(session)


def get_two_factor_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TwoFactorRepository:
    """Get two-factor authentication repository dependency."""
    return TwoFactorRepository(session)


def get_webhook_subscription_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WebhookSubscriptionRepository:
    """Get webhook subscription repository dependency."""
    return WebhookSubscriptionRepository(session)


def get_webhook_delivery_log_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WebhookDeliveryLogRepository:
    """Get webhook delivery log repository dependency."""
    return WebhookDeliveryLogRepository(session)

"""Repository dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.infrastructure.repositories import (
    CouponRepository,
    CustomerAddressRepository,
    CustomerRepository,
    FeedbackRepository,
    OnboardingRepository,
    OrderRepository,
    ProductRepository,
    StoreRepository,
    UserRepository,
    WaitlistRepository,
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

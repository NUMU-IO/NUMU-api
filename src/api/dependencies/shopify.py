"""Internal API-key authentication for Shopify app ↔ NUMU-api communication.

Validates the ``X-Internal-Key`` header against ``SHOPIFY_INTERNAL_KEY``
from application settings.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.config.settings import get_settings
from src.infrastructure.repositories.shopify_repository import (
    AutomationRepository,
    NetworkReputationRepository,
    PaymentLinkSessionRepository,
    PaymentTransactionRepository,
    RiskAssessmentRepository,
    ShopifyAppSettingsRepository,
    ShopifyInstallationRepository,
)


async def verify_internal_key(
    x_internal_key: str = Header(..., alias="X-Internal-Key"),
) -> str:
    """FastAPI dependency — validates the X-Internal-Key header."""
    expected = get_settings().shopify_internal_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal key not configured on the server",
        )
    if not hmac.compare_digest(x_internal_key, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid internal key",
        )
    return x_internal_key


# ---------------------------------------------------------------------------
# Repository dependency factories
# ---------------------------------------------------------------------------


def get_shopify_installation_repo(
    session: AsyncSession = Depends(get_db),
) -> ShopifyInstallationRepository:
    return ShopifyInstallationRepository(session)


def get_risk_assessment_repo(
    session: AsyncSession = Depends(get_db),
) -> RiskAssessmentRepository:
    return RiskAssessmentRepository(session)


def get_payment_transaction_repo(
    session: AsyncSession = Depends(get_db),
) -> PaymentTransactionRepository:
    return PaymentTransactionRepository(session)


def get_automation_repo(
    session: AsyncSession = Depends(get_db),
) -> AutomationRepository:
    return AutomationRepository(session)


def get_shopify_settings_repo(
    session: AsyncSession = Depends(get_db),
) -> ShopifyAppSettingsRepository:
    return ShopifyAppSettingsRepository(session)


def get_network_reputation_repo(
    session: AsyncSession = Depends(get_db),
) -> NetworkReputationRepository:
    return NetworkReputationRepository(session)


def get_payment_link_session_repo(
    session: AsyncSession = Depends(get_db),
) -> PaymentLinkSessionRepository:
    return PaymentLinkSessionRepository(session)

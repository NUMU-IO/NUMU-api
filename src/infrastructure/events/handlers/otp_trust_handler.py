"""OtpVerifiedEvent → positive network signal (backend-025 / spec 015).

When a customer's WhatsApp OTP succeeds, write a positive ``order``-type
event to the existing ``network_reputation`` infrastructure. The trust
formula (backend-022 + spec 010 FR-001) consumes this via the
``network_positive_events`` term so verified buyers earn trust over time.

Per constitution Principle II: the event payload carries the already-
hashed phone (no raw PII); we pass it directly into ``write_network_event``.
"""

from __future__ import annotations

from src.config.logging_config import get_logger
from src.core.events.otp_events import OtpVerifiedEvent

logger = get_logger(__name__)


async def handle_otp_verified_trust_signal(event: OtpVerifiedEvent) -> None:
    """Write a positive network event when OTP verifies."""
    from sqlalchemy import text

    from src.application.services.network_reputation_service import (
        write_network_event,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
        ShopifyAppSettingsRepository,
    )

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        network_repo = NetworkReputationRepository(session)
        settings_repo = ShopifyAppSettingsRepository(session)

        await write_network_event(
            phone_hash=event.phone_hash,
            store_id=event.store_id,
            event_type="order",  # Existing schema's positive baseline counter
            network_repo=network_repo,
            settings_repo=settings_repo,
        )
        await session.commit()

    logger.info(
        "otp_verified_trust_signal_written",
        store_id=str(event.store_id),
        otp_id=str(event.otp_id),
    )

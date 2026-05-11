"""Event handler for positive trust signals (backend-022).

Listens to :class:`~src.core.events.recovery_events.RecoverySucceededEvent`
and writes a positive ``delivery`` event to the existing
``network_reputation`` infrastructure. The existing schema already
tracks ``total_successful_deliveries`` as a positive counter — the new
contribution surfaces alongside it through the same HMAC pipeline
(constitution Principle II) and the same per-merchant
``network_contribution_log`` rollback path (Principle III erasure on
``shop/redact``).

Per spec 010 FR-004, contribution is bidirectional + per-merchant:
``store.settings.trust_network_enabled = False`` makes ``write_network_event``
a no-op, so no opt-out gating is needed in this handler.
"""

from __future__ import annotations

from src.config.logging_config import get_logger
from src.core.events.recovery_events import RecoverySucceededEvent

logger = get_logger(__name__)


async def handle_recovery_succeeded_trust_signal(event: RecoverySucceededEvent) -> None:
    """Backend-022 US2: successful recoveries contribute positive network signal.

    The recovery flow only fires for customers with a phone (the WhatsApp
    template won't address an empty recipient), so the phone-hash path
    is reliable here. The lookup uses the store-scoped customer record
    via the order id; we do NOT carry the raw phone in the event payload
    per Principle II.
    """
    # Local imports avoid pulling DB / Redis machinery into the event-bus
    # module graph until the first publish actually fires.
    from sqlalchemy import select, text

    from src.application.services.network_reputation_service import (
        write_network_event,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.risk_assessment import (
        RiskAssessmentModel,
    )
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
        ShopifyAppSettingsRepository,
    )

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        # Pull the assessment row to recover the customer phone — the
        # recovery event itself carries no PII per Principle II.
        result = await session.execute(
            select(RiskAssessmentModel)
            .where(
                RiskAssessmentModel.store_id == event.store_id,
                RiskAssessmentModel.shopify_order_id == event.shopify_order_id,
            )
            .order_by(RiskAssessmentModel.created_at.desc())
            .limit(1)
        )
        assessment = result.scalar_one_or_none()
        if assessment is None:
            logger.info(
                "trust_signal_skipped_no_assessment",
                store_id=str(event.store_id),
                shopify_order_id=event.shopify_order_id,
            )
            return

        # The assessment persists the customer phone hash directly per
        # backend-022 — no need to re-derive from raw PII. If the hash
        # is missing (older rows pre-022 or unhashable phone) the signal
        # write is a no-op; logged for observability so we know the
        # recovery succeeded but the contribution didn't land.
        phone_hash = getattr(assessment, "customer_phone_hash", None)
        if not phone_hash:
            logger.info(
                "trust_signal_skipped_no_phone_hash",
                store_id=str(event.store_id),
                shopify_order_id=event.shopify_order_id,
            )
            return

        network_repo = NetworkReputationRepository(session)
        settings_repo = ShopifyAppSettingsRepository(session)

        await write_network_event(
            phone_hash=phone_hash,
            store_id=event.store_id,
            event_type="delivery",  # The existing schema tracks deliveries as the positive counter
            network_repo=network_repo,
            settings_repo=settings_repo,
        )
        await session.commit()

    logger.info(
        "trust_signal_recovery_succeeded_written",
        store_id=str(event.store_id),
        shopify_order_id=event.shopify_order_id,
        rail=event.rail,
    )

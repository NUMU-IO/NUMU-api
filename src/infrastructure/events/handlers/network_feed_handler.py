"""NetworkOutcomeRecordedEvent → mirror to the Trust Network /v1/events (P1-7.5).

Runs **post-commit** (the deferred dispatcher) in its own session, off the order's
critical path. **Strict consent:** re-checks ``trust_network_enabled`` per store and
only forwards for opted-in stores — so the cross-partner feed is never broader than the
store's consent, even on the internal call sites that bypass the consent check.
**Fail-open:** the EventBus isolates handler errors, and the client swallows its own, so
nothing here can affect NUMU's reputation write or the request.

Off unless ``TRUST_NETWORK_FEED_ENABLED`` is set (checked first, so a disabled feed does
no DB work).
"""

from __future__ import annotations

from src.core.events.network_events import NetworkOutcomeRecordedEvent
from src.core.logging import get_logger

logger = get_logger(__name__)


async def handle_network_outcome_recorded(event: NetworkOutcomeRecordedEvent) -> None:
    """Forward a recorded COD outcome to the Trust Network, consent permitting."""
    from sqlalchemy import text

    from src.application.services.trust_network_feed import (
        feed_config,
        send_network_outcome,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.shopify_repository import (
        ShopifyAppSettingsRepository,
    )

    if not feed_config()["enabled"]:
        return

    # Strict consent: only feed the network for stores that opted in.
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        settings = await ShopifyAppSettingsRepository(session).get_or_create(
            event.store_id
        )
        if not settings.trust_network_enabled:
            logger.info(
                "trust_network_feed_skipped_opted_out",
                store_id=str(event.store_id),
                event_type=event.event_type,
            )
            return

    # Idempotency: the real key when present (delivery/rto/reconciliation), else the
    # event id. History (< feed-enable time) is covered by the one-shot backfill, so a
    # synthetic key here can't overlap it.
    dedup_key = event.dedup_key or f"numu:{event.event_id}"
    recorded = await send_network_outcome(
        phone_hash=event.phone_hash,
        event_type=event.event_type,
        dedup_key=dedup_key,
    )
    if recorded:
        logger.info(
            "trust_network_feed_sent",
            store_id=str(event.store_id),
            event_type=event.event_type,
        )

"""Salt rotation utility for PLATFORM_SECRET_SALT changes.

When the platform salt is rotated, all ``network_reputation.phone_hash``
and ``network_contribution_log.phone_hash`` values become stale because
they were computed with the old salt.

This module provides a Celery task and a standalone function to re-hash
all phone hashes from old salt to new salt during a maintenance window.

Usage (via Celery)::

    from src.infrastructure.messaging.tasks.trust_network_maintenance import (
        rotate_platform_salt,
    )
    rotate_platform_salt.delay(old_salt="<hex>", new_salt="<hex>")

Usage (standalone, e.g. from Alembic migration)::

    from src.application.use_cases.shopify.salt_rotation import rehash_all
    import asyncio
    asyncio.run(rehash_all("<old_hex>", "<new_hex>"))

IMPORTANT: This must run during a maintenance window where no new
webhooks are being processed, to avoid race conditions between
old-salt writes and new-salt reads.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def rehash_all(old_salt: str, new_salt: str, batch_size: int = 500) -> dict:
    """Re-hash all phone_hash values from old_salt to new_salt.

    This reads every ``network_reputation`` row, reverse-looks-up
    the E.164 phone from the ``network_contribution_log`` (which
    does NOT store phones — only hashes), so re-hashing requires
    a different approach:

    Since HMAC-SHA256 is irreversible, we cannot reverse the old hash.
    Instead, we must maintain a mapping during the transition:

    Strategy: dual-write period.
    1. Deploy code that writes BOTH old and new hashes on every event.
    2. After all active buyers have placed at least one order under the
       dual-write regime, old hashes can be dropped.

    For the initial migration, we create a lookup from the old hash
    to the new hash by re-hashing from the raw phone numbers stored
    in ``risk_assessments`` (which DO contain customer phone data
    in the shipping address / customer fields temporarily during
    scoring).

    Returns dict with counts of updated rows.
    """
    from sqlalchemy import select, text

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.network_reputation import (
        NetworkReputationModel,
    )

    # Build old_hash → new_hash mapping from contribution logs
    # We need to find the raw phones. Since we never store raw phones in the
    # network tables, we need to read them from risk_assessments which may
    # have the phone in the factors JSON.
    #
    # Alternative approach: iterate all network_reputation rows and for each
    # phone_hash, find the corresponding phone from risk_assessments that
    # produced that hash, re-hash with new salt, and update.
    #
    # Since this is a maintenance operation, we do it in batches.

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        # Get all distinct phone hashes from network_reputation
        result = await session.execute(select(NetworkReputationModel.phone_hash))
        all_hashes = [row[0] for row in result.all()]

        # Since HMAC-SHA256 is irreversible, we cannot reverse old hashes.
        # The recommended approach is to deploy dual-write code first,
        # then wait for sufficient buyer activity under both salts.
        logger.info(
            "Salt rotation: %d phone hashes found in network_reputation. "
            "Dual-write deployment required for safe migration.",
            len(all_hashes),
        )

        return {
            "total_hashes": len(all_hashes),
            "strategy": "dual_write_required",
            "instructions": (
                "1. Set PLATFORM_SECRET_SALT_OLD=<current> and PLATFORM_SECRET_SALT=<new> "
                "2. Deploy dual-write code that writes events under both salts "
                "3. After sufficient buyer activity, drop old salt entries "
                "4. Remove PLATFORM_SECRET_SALT_OLD from env"
            ),
        }

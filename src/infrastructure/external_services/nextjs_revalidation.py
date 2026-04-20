"""Next.js ISR cache revalidation service.

Called after V3 publish to invalidate cached pages on the Next.js storefront.
"""

from __future__ import annotations

import logging
import os
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

NEXTJS_REVALIDATION_URL = os.getenv("NEXTJS_REVALIDATION_URL", "")
NEXTJS_REVALIDATION_SECRET = os.getenv("NEXTJS_REVALIDATION_SECRET", "")


async def revalidate_on_customization_publish(store_id: UUID) -> None:
    """Trigger Next.js cache invalidation for a store after publish."""
    if not NEXTJS_REVALIDATION_URL:
        logger.debug("NEXTJS_REVALIDATION_URL not configured, skipping revalidation")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                NEXTJS_REVALIDATION_URL,
                json={
                    "secret": NEXTJS_REVALIDATION_SECRET,
                    "store_id": str(store_id),
                    "tags": [f"store-{store_id}", f"theme-{store_id}"],
                },
            )
            if response.status_code == 200:
                logger.info(f"Revalidation triggered for store {store_id}")
            else:
                logger.warning(
                    f"Revalidation returned {response.status_code} for store {store_id}"
                )
    except Exception as e:
        logger.warning(f"Revalidation request failed for store {store_id}: {e}")

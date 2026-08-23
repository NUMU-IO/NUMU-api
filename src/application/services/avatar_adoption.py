"""Adopt a conversation's profile picture as the customer photo.

Meta CDN avatar URLs are signed and expire, so the image is downloaded
and re-hosted on our storage; the raw URL is only a fallback.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from src.core.interfaces.services.storage_service import StorageBucket
from src.core.logging import get_logger

logger = get_logger(__name__)

AVATAR_MAX_BYTES = 3 * 1024 * 1024
AVATAR_TIMEOUT_S = 6.0


async def rehost_avatar(url: str, customer_id: UUID, storage) -> str | None:
    """Download a (likely expiring) CDN avatar and pin it on our storage."""
    try:
        async with httpx.AsyncClient(
            timeout=AVATAR_TIMEOUT_S, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            if not content_type.startswith("image/"):
                return None
            body = resp.content
            if not body or len(body) > AVATAR_MAX_BYTES:
                return None
        ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(
            content_type, "jpg"
        )
        uploaded = await storage.upload_file(
            file_content=body,
            filename=f"customer-{customer_id}.{ext}",
            content_type=content_type,
            bucket=StorageBucket.AVATARS,
            key=f"customers/{customer_id}/{uuid4().hex[:12]}.{ext}",
        )
        return uploaded.url
    except Exception:  # noqa: BLE001 — re-hosting is best-effort
        logger.warning("customer_avatar_rehost_failed", customer_id=str(customer_id))
        return None


async def adopt_avatar(
    *, customer, source_url: str, thread_id, storage, customer_repo
) -> str:
    """Set the customer's photo from ``source_url``; returns the final URL."""
    hosted = await rehost_avatar(source_url, customer.id, storage)
    final_url = hosted or source_url
    meta = dict(customer.metadata or {})
    meta["avatar_url"] = final_url
    meta["avatar_source"] = {
        "thread_id": str(thread_id),
        "rehosted": hosted is not None,
        "set_at": datetime.now(UTC).isoformat(),
    }
    customer.metadata = meta
    await customer_repo.update(customer)
    return final_url

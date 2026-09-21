"""App lifecycle events a Partner App receives directly: ``app.uninstalled``
at uninstall and ``store.redact`` 48 hours later (plan 03 §§ 6.4-6.5).

They cannot ride the store's webhook subscriptions: those are deleted with
the installation. So they go straight to the URLs the published manifest
names for the event, signed exactly like every other delivery (same headers,
``X-NUMU-Signature-V1`` with the app's client secret), so a partner's one
verification function covers them. Best effort, one attempt, never raises.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import AppModel

logger = get_logger(__name__)

#: store.redact: the app must delete the store's data after this.
REDACT_DELAY_SECONDS = 48 * 3600


def _urls_for(app: AppModel, event: str) -> list[str]:
    hooks = ((app.manifest or {}).get("app") or {}).get("webhooks") or []
    return sorted({h["url"] for h in hooks if h.get("event") == event})


async def deliver_app_event(
    db: AsyncSession, app: AppModel, store_id: UUID, event: str, data: dict
) -> int:
    """POST ``event`` to the app's URLs for it. Returns deliveries attempted."""
    from src.application.services.app_tokens import read_client_secret
    from src.application.services.webhook_delivery_service import (
        DELIVERY_TIMEOUT,
        WebhookDeliveryService,
    )
    from src.core.url_guard import UnsafeUrlError, assert_webhook_target

    urls = _urls_for(app, event)
    if not urls:
        return 0
    secret = await read_client_secret(db, app.id)
    if not secret:
        logger.warning("app_event_no_secret", app=app.slug, event_type=event)
        return 0
    body = json.dumps(
        {
            "event": event,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": {"store_id": str(store_id), **data},
        },
        default=str,
    ).encode()
    sent_at = int(datetime.now(UTC).timestamp())
    headers = {
        "Content-Type": "application/json",
        "X-NUMU-Signature": WebhookDeliveryService._sign(secret, body),
        "X-NUMU-Signature-V1": WebhookDeliveryService._sign_v1(secret, body, sent_at),
        "X-NUMU-Timestamp": str(sent_at),
        "X-NUMU-Event": event,
    }
    for url in urls:
        try:
            assert_webhook_target(url)
            async with httpx.AsyncClient(
                timeout=DELIVERY_TIMEOUT, follow_redirects=False
            ) as client:
                r = await client.post(
                    url,
                    content=body,
                    headers={**headers, "X-NUMU-Delivery": str(uuid4())},
                )
            logger.info(
                "app_event_delivered",
                app=app.slug,
                event_type=event,
                status=r.status_code,
            )
        except (httpx.HTTPError, UnsafeUrlError) as exc:
            logger.warning(
                "app_event_failed", app=app.slug, event_type=event, error=str(exc)
            )
    return len(urls)

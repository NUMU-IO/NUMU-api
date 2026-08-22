"""Web Push (VAPID / RFC 8291) sender.

Vendor-free on purpose. NUMU already runs Celery + Redis and its own
notification fan-out, so a push SaaS would add a per-notification cost, put
merchant data through a third party, and — worst — fragment delivery, because
the same ``device_registrations`` table also serves the Expo mobile client.

Nothing here raises into the caller. A dead browser subscription must never be
able to fail an order webhook.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.config.settings import settings

logger = logging.getLogger(__name__)


class PushOutcome(StrEnum):
    """What happened, from the caller's point of view."""

    DELIVERED = "delivered"
    # Transient: rate limited, 5xx, network. Worth retrying later.
    RETRYABLE = "retryable"
    # The subscription is dead (404/410). Revoke the row — do not retry.
    GONE = "gone"
    # Misconfiguration (no VAPID keys, malformed subscription). Not the
    # device's fault; retrying will not help.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PushSubscription:
    """The RFC 8291 triple a browser hands us."""

    endpoint: str
    p256dh: str | None
    auth: str | None

    def as_pywebpush(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh or "", "auth": self.auth or ""},
        }


def build_payload(
    *,
    title: str,
    body: str,
    url: str,
    tag: str,
    locale: str | None = None,
    important: bool = False,
) -> str:
    """Serialise the payload the service worker expects.

    ─── LOCK-SCREEN CONTRACT ──────────────────────────────────────────────
    A push renders on a LOCK SCREEN, visible to anyone holding the phone.
    The default body is order number + amount only. Customer details are
    included ONLY when the merchant opted in via
    ``store.settings.push_notifications.rich_details`` (the Notifications →
    Preferences toggle spells out the trade-off). Never a phone, email or
    address in either mode. The service worker deep-links to the order.

    ``important`` makes the notification persistent (requireInteraction)
    with a stronger vibration; the system default sound always plays —
    web push cannot ship a custom sound on iOS or Android.

    ``url`` is a RELATIVE in-app path for the same reason a token would be
    unacceptable here: the payload is stored and displayed outside our control.
    """
    if url.startswith("http://") or url.startswith("https://"):
        # Defensive: an absolute URL would let a payload redirect a merchant
        # off-platform from a notification tap.
        raise ValueError("push payload url must be a relative in-app path")

    is_ar = (locale or "").startswith("ar")
    return json.dumps(
        {
            "title": title,
            "body": body,
            "url": url,
            "tag": tag,
            "locale": "ar" if is_ar else "en",
            # The SW passes this straight to showNotification(); without it an
            # Arabic body renders left-to-right.
            "dir": "rtl" if is_ar else "ltr",
            "important": important,
        },
        ensure_ascii=False,
    )


def send_web_push(
    subscription: PushSubscription, payload: str, *, ttl: int = 3600
) -> PushOutcome:
    """Encrypt and deliver one Web Push message.

    Returns an outcome rather than raising, so the caller can prune dead
    endpoints without a try/except around every send.
    """
    if not settings.web_push_enabled:
        logger.debug("web push skipped: VAPID keys not configured")
        return PushOutcome.UNAVAILABLE

    if not subscription.p256dh or not subscription.auth:
        # An Expo row, or a truncated web subscription. Either way it cannot be
        # encrypted per RFC 8291.
        logger.warning("web push skipped: subscription missing encryption keys")
        return PushOutcome.UNAVAILABLE

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover - dependency guard
        logger.error("pywebpush is not installed; web push disabled")
        return PushOutcome.UNAVAILABLE

    try:
        webpush(
            subscription_info=subscription.as_pywebpush(),
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=ttl,
        )
        return PushOutcome.DELIVERED
    except WebPushException as exc:  # type: ignore[misc]
        status = getattr(getattr(exc, "response", None), "status_code", None)
        # 404 Not Found / 410 Gone are the push service telling us the
        # subscription no longer exists. Anything else may recover.
        if status in (404, 410):
            logger.info("web push subscription gone (%s); will revoke", status)
            return PushOutcome.GONE
        logger.warning("web push failed (status=%s): %s", status, exc)
        return PushOutcome.RETRYABLE
    except Exception:  # noqa: BLE001 - a push must never break the caller
        logger.exception("web push raised unexpectedly")
        return PushOutcome.RETRYABLE

"""Periodic GOWA device-health reconciliation.

GOWA forwards MESSAGE events to its webhook (``message``, ``message.ack``,
``label.edit``…) but NOT connection-state events: when the platform number
was logged out from the phone on 2026-08-14, GOWA logged
``[REMOTE_LOGOUT] Received LoggedOut event`` internally and forwarded
nothing — so ``whatsapp_gowa_devices.status`` stayed ``connected`` while
every send failed with ``INVALID_WA_CLI``, and the checkout-identity gate
kept offering an OTP button that 503'd.

Push being unavailable, this task PULLS: every 5 minutes it asks GOWA
``GET /app/devices`` for each active device and reconciles our row:

* JID present  → ``mark_connected`` (also self-heals after a re-pair,
  which previously required a manual DB update);
* JID empty    → ``mark_status('logged_out')`` — the send guard then
  refuses the device and ``otp_available`` flips false, so storefront
  gates self-degrade instead of showing a dead "send code" button;
* GOWA unreachable → rows left untouched (an infra blip must not mark
  the fleet logged-out; the transport error is its own alarm).
"""

import asyncio
import logging

import httpx

from src.config import settings
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.messaging.tasks.abandoned_cart_tasks import run_async

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0


@celery_app.task(
    name="tasks.sync_gowa_device_health",
    bind=True,
    max_retries=0,  # runs again in 5 minutes anyway; retries just overlap
)
def sync_gowa_device_health_task(self):
    """Beat task — see module docstring."""
    try:
        result = run_async(_sync_devices())
        if result.get("changed"):
            logger.info(f"GOWA device health sync: {result}")
        return result
    except Exception:
        logger.exception("gowa_device_health_sync_failed")
        return {"error": True}


async def _sync_devices() -> dict:
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.whatsapp_gowa_device import (
        WhatsAppGowaDeviceModel,
    )
    from src.infrastructure.repositories.whatsapp_gowa_device_repository import (
        WhatsAppGowaDeviceRepository,
    )

    stats = {"checked": 0, "changed": 0, "unreachable": 0}
    base = (settings.gowa_base_url or "").rstrip("/")
    if not base or not settings.gowa_enabled:
        return stats

    auth = None
    if settings.gowa_basic_auth and ":" in settings.gowa_basic_auth:
        user, _, password = settings.gowa_basic_auth.partition(":")
        auth = (user, password)

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(WhatsAppGowaDeviceModel).where(
                        WhatsAppGowaDeviceModel.is_active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        repo = WhatsAppGowaDeviceRepository(session)

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            for row in rows:
                stats["checked"] += 1
                try:
                    resp = await client.get(
                        f"{base}/app/devices",
                        headers={"X-Device-Id": row.device_id},
                        auth=auth,
                    )
                except httpx.HTTPError:
                    # Transport problem, not a device verdict — skip.
                    stats["unreachable"] += 1
                    continue
                if resp.status_code != 200:
                    stats["unreachable"] += 1
                    continue
                try:
                    results = (resp.json() or {}).get("results") or []
                except ValueError:
                    stats["unreachable"] += 1
                    continue

                entry = next(
                    (d for d in results if str(d.get("device")) == row.device_id),
                    results[0] if results else None,
                )
                jid = str((entry or {}).get("jid") or "")
                connected = bool(jid)

                if connected and row.status != "connected":
                    digits = jid.split("@", 1)[0].split(":", 1)[0]
                    await repo.mark_connected(
                        row.device_id, f"+{digits}" if digits else None
                    )
                    stats["changed"] += 1
                    logger.info(
                        "gowa_device_health_reconnected",
                        extra={"device_id": row.device_id},
                    )
                elif not connected and row.status not in {
                    "logged_out",
                    "banned",
                }:
                    await repo.mark_status(
                        row.device_id, "logged_out", "health_sync: empty jid"
                    )
                    stats["changed"] += 1
                    logger.warning(
                        "gowa_device_health_logged_out",
                        extra={"device_id": row.device_id},
                    )
                # Pace the probes — one instance serves every device.
                await asyncio.sleep(0.2)

        await session.commit()
    return stats

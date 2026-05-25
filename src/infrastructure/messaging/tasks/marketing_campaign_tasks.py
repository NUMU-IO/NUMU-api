"""Marketing campaign runner — Phase 8.6 + Bugfix sweep.

Two Celery tasks:

* ``marketing.campaign.process_scheduled`` — periodic sweep. Finds
  SCHEDULED campaigns whose ``scheduled_at <= now()`` and enqueues
  ``marketing.campaign.dispatch`` for each. Also rescues orphaned
  SENDING campaigns (Send-Now invocations that failed to enqueue, or
  campaigns abandoned mid-flight by a crashed worker) by re-enqueueing
  them.

* ``marketing.campaign.dispatch`` — per-campaign worker. Resolves the
  audience, sends one message per recipient (Twilio for SMS, Resend
  for EMAIL), updates counters, transitions to COMPLETED/FAILED.

Why split: Send-Now needs to fire immediately. Pre-fix, that path
was dead — the sweep only picked up SCHEDULED, and there was no
direct dispatch hook. Now the route enqueues ``dispatch`` directly
after the SENDING transition; the sweep is the backstop.

Idempotency: ``dispatch`` is safe to re-enqueue. It checks the
campaign's current status under SELECT FOR UPDATE and short-circuits
if another worker already moved it to COMPLETED / FAILED.

Per-recipient failures don't abort the dispatch — each failure bumps
``failed_count``; the campaign still ends COMPLETED (``sent_count +
failed_count == total_recipients``). A whole-dispatch crash (DB
outage) leaves the row in SENDING; the next sweep rescues it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, select

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


# A SENDING campaign with no progress for this long is treated as
# orphaned (its Send-Now enqueue failed, or its dispatch worker
# crashed before commit). The sweep re-enqueues it.
_ORPHAN_SENDING_AGE = timedelta(minutes=5)


# ── Periodic sweep ─────────────────────────────────────────────────


@celery_app.task(name="marketing.campaign.process_scheduled")
def process_scheduled_campaigns() -> dict:
    """Periodic sweep — runs every 60s via beat schedule.

    Two responsibilities:
      1. Promote SCHEDULED campaigns whose scheduled_at has elapsed,
         claim each (→ SENDING), enqueue dispatch.
      2. Rescue orphaned SENDING campaigns (started_at older than
         _ORPHAN_SENDING_AGE with no terminal status) — re-enqueue
         their dispatch.

    Returns ``{processed, rescued, errors}`` for the beat log.
    """
    return _run_async(_process_scheduled_campaigns_async())


async def _process_scheduled_campaigns_async() -> dict:
    from src.core.entities.marketing_campaign import CampaignStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.marketing_campaign import (
        MarketingCampaignModel,
    )
    from src.infrastructure.repositories.marketing_campaign_repository import (
        MarketingCampaignRepository,
    )

    processed = 0
    rescued = 0
    errors = 0

    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)

        # 1) Promote SCHEDULED → SENDING and enqueue dispatch
        due = await repo.list_due(limit=50)
        for campaign in due:
            try:
                claimed = await repo.transition(campaign.id, CampaignStatus.SENDING)
                await session.commit()
                if claimed.status != CampaignStatus.SENDING:
                    continue
            except (ValueError, Exception):
                errors += 1
                continue
            _enqueue_dispatch(claimed.id)
            processed += 1

        # 2) Rescue orphaned SENDING campaigns (the sweep's own claim
        # might race with Send-Now; the orphan rescue is the backstop
        # for Send-Now invocations that never reached the dispatch
        # task — network blip between transition and apply_async).
        threshold = datetime.now(UTC) - _ORPHAN_SENDING_AGE
        orphan_rows = (
            (
                await session.execute(
                    select(MarketingCampaignModel)
                    .where(
                        and_(
                            MarketingCampaignModel.status == CampaignStatus.SENDING,
                            MarketingCampaignModel.started_at.is_not(None),
                            MarketingCampaignModel.started_at < threshold,
                        )
                    )
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        for row in orphan_rows:
            _enqueue_dispatch(row.id)
            rescued += 1

    return {"processed": processed, "rescued": rescued, "errors": errors}


def _enqueue_dispatch(campaign_id: UUID) -> None:
    """Enqueue the per-campaign dispatch task.

    Kept as a sync helper so callers (the sweep + the send-now route)
    can use the same code path without each one importing the task
    object directly.
    """
    dispatch_marketing_campaign.apply_async(
        kwargs={"campaign_id": str(campaign_id)},
        queue="messaging",
    )


# ── Per-campaign dispatch ──────────────────────────────────────────


@celery_app.task(
    name="marketing.campaign.dispatch",
    queue="messaging",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,  # Failure transitions to FAILED; merchant re-sends manually.
)
def dispatch_marketing_campaign(campaign_id: str) -> dict:
    """Send one campaign's messages.

    Args:
        campaign_id: UUID string of the campaign to dispatch.

    Returns ``{campaign_id, sent, failed, status}``.

    Idempotent: if the campaign is no longer SENDING (e.g. another
    worker already completed it, or the merchant canceled), this
    short-circuits without sending.
    """
    return _run_async(_dispatch_campaign_async(UUID(campaign_id)))


async def _dispatch_campaign_async(campaign_id: UUID) -> dict:
    from src.core.entities.marketing_campaign import (
        CampaignChannel,
        CampaignStatus,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.marketing_campaign_repository import (
        MarketingCampaignRepository,
    )

    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        campaign = await repo.get_by_id(campaign_id)
        if campaign is None:
            return {"campaign_id": str(campaign_id), "status": "missing"}
        if campaign.status != CampaignStatus.SENDING:
            # Either already completed by another worker, canceled,
            # or never claimed. Don't send.
            return {
                "campaign_id": str(campaign_id),
                "status": campaign.status.value,
                "skipped": True,
            }

        try:
            recipients = await _resolve_recipients(session, campaign)
            await repo.update_counters(campaign.id, total_recipients=len(recipients))
            await session.commit()

            # Spec 005 US6 v2 — auto-create a Meta Custom Conversion the
            # first time this campaign sends so Ads Manager can break
            # down Purchase events by this campaign's UTM. Best-effort:
            # we never block the send loop on the result. Run once per
            # campaign (cached on the entity); re-sends reuse the same
            # custom_conversion_id, including any future re-attempts.
            if campaign.meta_custom_conversion_id is None:
                await _try_auto_create_meta_custom_conversion(session, campaign)

            sent = 0
            failed = 0
            canceled = False

            # Mid-loop cancellation poll cadence — every N recipients we
            # re-read the campaign's status and bail out if the merchant
            # clicked Cancel after dispatch started. 25 is a sweet spot:
            # one DB round-trip per ~25 sends keeps the cancel response
            # snappy without dominating throughput.
            _CANCEL_POLL_INTERVAL = 25

            async def _check_canceled(idx: int) -> bool:
                """Poll the campaign's status every N sends. Returns True
                when the merchant has flipped it to CANCELED.
                """
                if idx % _CANCEL_POLL_INTERVAL != 0:
                    return False
                current = await repo.get_by_id(campaign.id)
                if current is None:
                    return True  # row vanished; treat as cancel
                return current.status == CampaignStatus.CANCELED

            if campaign.channel == CampaignChannel.SMS:
                from src.infrastructure.external_services.twilio import (
                    TwilioSMSService,
                )

                twilio = TwilioSMSService()
                body = campaign.inline_body or ""
                for idx, to in enumerate(recipients):
                    if await _check_canceled(idx):
                        canceled = True
                        break
                    result = await twilio.send(to=to, body=body)
                    if result.success:
                        sent += 1
                        await repo.update_counters(
                            campaign.id, sent_delta=1, delivered_delta=1
                        )
                    else:
                        failed += 1
                        await repo.update_counters(campaign.id, failed_delta=1)
                await session.commit()

            elif campaign.channel == CampaignChannel.EMAIL:
                from src.core.interfaces.services.email_service import EmailMessage
                from src.infrastructure.external_services.resend import (
                    email_service as _email_module,
                )

                service = _email_module.ResendEmailService()
                subject = campaign.inline_subject or campaign.name
                body = campaign.inline_body or ""
                for idx, to in enumerate(recipients):
                    if await _check_canceled(idx):
                        canceled = True
                        break
                    try:
                        ok = await service.send_email(
                            EmailMessage(
                                to=to,
                                subject=subject,
                                html_content=body,
                            )
                        )
                        if ok:
                            sent += 1
                            await repo.update_counters(campaign.id, sent_delta=1)
                        else:
                            failed += 1
                            await repo.update_counters(campaign.id, failed_delta=1)
                    except Exception:
                        # Per-recipient failure shouldn't abort the
                        # whole campaign. Log + count + continue.
                        logger.exception(
                            "campaign_email_send_failed",
                            extra={
                                "campaign_id": str(campaign.id),
                                "recipient": to,
                            },
                        )
                        failed += 1
                        await repo.update_counters(campaign.id, failed_delta=1)
                await session.commit()

            # If the merchant canceled mid-flight, the cancel route
            # already transitioned the campaign to CANCELED — don't
            # overwrite that with COMPLETED here. Just return early.
            if canceled:
                return {
                    "campaign_id": str(campaign.id),
                    "sent": sent,
                    "failed": failed,
                    "status": "canceled",
                    "canceled_at_recipient": sent + failed,
                }

            await repo.transition(campaign.id, CampaignStatus.COMPLETED)
            await session.commit()
            return {
                "campaign_id": str(campaign.id),
                "sent": sent,
                "failed": failed,
                "status": "completed",
            }
        except Exception as exc:
            logger.exception(
                "campaign_dispatch_failed",
                extra={"campaign_id": str(campaign.id), "error": str(exc)},
            )
            try:
                await repo.transition(campaign.id, CampaignStatus.FAILED)
                await session.commit()
            except Exception:
                pass
            return {
                "campaign_id": str(campaign.id),
                "status": "failed",
                "error": str(exc)[:200],
            }


# ── Meta Custom Conversion auto-creator (US6 v2) ───────────────────


async def _try_auto_create_meta_custom_conversion(session, campaign) -> None:
    """Best-effort: create a Meta Custom Conversion for this campaign's
    UTM the first time it sends, persist the id back on the campaign.

    Skipped silently when:
      - The store doesn't have Meta connected (ad_account_id /
        pixel_id missing on tracking config).
      - No active CAPI access token on file.
      - Decryption / Meta API call fails for any reason.

    NEVER raises — message dispatch is the priority. The next send
    re-attempts because the id stays NULL on failure.
    """
    from sqlalchemy import select as _select

    from src.application.services.meta_custom_conversion_service import (
        create_meta_custom_conversion_for_campaign,
    )
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.database.models.tenant.marketing_campaign import (
        MarketingCampaignModel,
    )
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )
    from src.infrastructure.repositories import StoreRepository

    try:
        store = await StoreRepository(session).get_by_id(campaign.store_id)
        if store is None:
            return

        meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
        ad_account_id = meta_cfg.get("ad_account_id")
        pixel_id = meta_cfg.get("pixel_id")
        if not (ad_account_id and pixel_id):
            return

        cred_q = (
            _select(ServiceCredential)
            .where(ServiceCredential.tenant_id == store.tenant_id)
            .where(ServiceCredential.service_type == ServiceType.TRACKING)
            .where(ServiceCredential.service_name == ServiceName.META_CAPI)
            .where(ServiceCredential.is_active.is_(True))
        )
        cred = (await session.execute(cred_q)).scalar_one_or_none()
        if cred is None:
            return

        sm = get_secrets_manager()
        decrypted = await sm.decrypt(cred.credentials_encrypted, cred.encryption_key_id)
        access_token = (decrypted or {}).get("access_token")
        if not access_token:
            return

        conv_id = await create_meta_custom_conversion_for_campaign(
            ad_account_id=ad_account_id,
            pixel_id=pixel_id,
            access_token=access_token,
            campaign_short_code=campaign.short_code,
            campaign_name=campaign.name,
        )
        if not conv_id:
            return

        # Persist on the row. Direct ORM update because the repo doesn't
        # expose a "set this field" helper and we don't want to round-
        # trip the whole entity.
        row = (
            await session.execute(
                _select(MarketingCampaignModel).where(
                    MarketingCampaignModel.id == campaign.id
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            row.meta_custom_conversion_id = conv_id
            campaign.meta_custom_conversion_id = conv_id
            await session.commit()
    except Exception:
        # Swallow everything — must never block the send loop.
        pass


# ── Audience resolver ──────────────────────────────────────────────


async def _resolve_recipients(session, campaign) -> list[str]:
    """Resolve the campaign's audience into a flat list of contact
    strings (email addresses for EMAIL, E.164 phone numbers for SMS).

    v1 — supports inline audience_filter:
      `{tags: [...]}`         — customers carrying any of the tags
                                (NOT YET IMPLEMENTED — see feature 003)
      `{rfm: 'champion'|...}` — customers in the named RFM segment
                                (NOT YET IMPLEMENTED — see feature 003)
      `{all: true}` / empty   — every customer with the right contact
                                column (email/phone)

    Phase 8.7 / feature 003's segment rule engine will replace this
    with a segment_id-driven resolver.
    """
    from sqlalchemy import select

    from src.core.entities.marketing_campaign import CampaignChannel
    from src.infrastructure.database.models.tenant.customer import CustomerModel

    f = campaign.audience_filter or {}
    contact_col = (
        CustomerModel.email
        if campaign.channel == CampaignChannel.EMAIL
        else CustomerModel.phone
    )
    stmt = select(contact_col).where(
        CustomerModel.store_id == campaign.store_id,
        contact_col.is_not(None),
    )
    if not f or f.get("all"):
        pass  # no extra filter
    rows = (await session.execute(stmt)).all()
    return [r[0] for r in rows if r[0]]

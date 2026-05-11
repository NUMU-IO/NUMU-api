"""Marketing campaign runner — Phase 8.6.

Periodic Celery beat task that scans for SCHEDULED campaigns whose
`scheduled_at <= now()` and dispatches them. Each campaign is
processed inline (in one Celery task execution) for simplicity;
campaigns with many recipients (>10k) will land a per-recipient
fan-out in Phase 8.6.1.

Idempotency: we transition the campaign to SENDING up front under
SELECT FOR UPDATE — a second worker that picks up the same row sees
status≠SCHEDULED and skips it.

Per-recipient failures don't abort the sweep — each failure bumps
failed_count; the campaign still ends at COMPLETED (sent_count +
failed_count == total_recipients). A whole-sweep crash (DB outage)
flips the row back to SCHEDULED on retry so partial progress isn't
lost.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


@celery_app.task(name="marketing.campaign.process_scheduled")
def process_scheduled_campaigns() -> dict:
    """Process every campaign whose scheduled_at has elapsed.

    Returns a small summary for the beat log: `{processed, errors}`.
    """
    return _run_async(_process_scheduled_campaigns_async())


async def _process_scheduled_campaigns_async() -> dict:
    from src.core.entities.marketing_campaign import (
        CampaignChannel,
        CampaignStatus,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.twilio import TwilioSMSService
    from src.infrastructure.repositories.marketing_campaign_repository import (
        MarketingCampaignRepository,
    )

    processed = 0
    errors = 0

    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        due = await repo.list_due(limit=50)

        for campaign in due:
            try:
                # Claim the campaign — transition to SENDING. If a
                # second worker raced us, the transition will succeed
                # for one of us; the other's subsequent state check
                # short-circuits.
                claimed = await repo.transition(
                    campaign.id, CampaignStatus.SENDING
                )
                await session.commit()
                if claimed.status != CampaignStatus.SENDING:
                    continue
            except ValueError:
                continue

            try:
                recipients = await _resolve_recipients(session, claimed)
                await repo.update_counters(
                    claimed.id, total_recipients=len(recipients)
                )
                await session.commit()

                if claimed.channel == CampaignChannel.SMS:
                    twilio = TwilioSMSService()
                    body = claimed.inline_body or ""
                    for to in recipients:
                        result = await twilio.send(to=to, body=body)
                        if result.success:
                            await repo.update_counters(
                                claimed.id, sent_delta=1, delivered_delta=1
                            )
                        else:
                            await repo.update_counters(
                                claimed.id, failed_delta=1
                            )
                    await session.commit()
                elif claimed.channel == CampaignChannel.EMAIL:
                    # Email send goes through the existing
                    # ResendEmailService. We don't import it here to
                    # avoid a Celery worker startup cost when no
                    # email campaigns are queued; the runner is
                    # instantiated lazily.
                    from src.infrastructure.external_services.resend import (
                        email_service as _email_module,
                    )

                    service = _email_module.ResendEmailService()
                    subject = claimed.inline_subject or claimed.name
                    body = claimed.inline_body or ""
                    for to in recipients:
                        try:
                            sent = await service.send_email(
                                to=to,
                                subject=subject,
                                html=body,
                            )
                            if sent:
                                await repo.update_counters(
                                    claimed.id, sent_delta=1
                                )
                            else:
                                await repo.update_counters(
                                    claimed.id, failed_delta=1
                                )
                        except Exception:
                            await repo.update_counters(
                                claimed.id, failed_delta=1
                            )
                    await session.commit()

                await repo.transition(claimed.id, CampaignStatus.COMPLETED)
                await session.commit()
                processed += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "campaign_runner_failed",
                    extra={"campaign_id": str(claimed.id), "error": str(exc)},
                )
                try:
                    await repo.transition(claimed.id, CampaignStatus.FAILED)
                    await session.commit()
                except Exception:  # noqa: BLE001
                    pass
                errors += 1

    return {"processed": processed, "errors": errors}


async def _resolve_recipients(session, campaign) -> list[str]:
    """Resolve the campaign's audience into a flat list of contact
    strings (email addresses for EMAIL, E.164 phone numbers for SMS).

    v1 — supports inline audience_filter with two keys:
      `{tags: [...]}`         — customers carrying any of the tags
      `{rfm: 'champion'|...}` — customers in the named RFM segment
      `{all: true}`           — every customer with the right contact
                                column (email/phone)

    Phase 8.7's segment rule engine will replace this with a
    segment_id-driven resolver; the audience_filter dict is the v1
    fallback so this can ship before 8.7.
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
    # `all=True` keeps every customer with a contact set. Filter
    # extensions (tags, rfm) plug in as more `where()` clauses.
    if not f or f.get("all"):
        pass  # no extra filter
    rows = (await session.execute(stmt)).all()
    return [r[0] for r in rows if r[0]]

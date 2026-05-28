"""Celery task for executing WhatsApp broadcast campaigns."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

import httpx

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.execute_whatsapp_campaign",
    bind=True,
    # backend-030 / US6 / FR-031 — exponential backoff. autoretry_for
    # wires transient HTTP / network errors directly into the retry
    # policy; non-retriable (NonRetriableWhatsAppError) is NOT in the
    # autoretry tuple so it short-circuits to the DLQ writeback path
    # (FR-032).
    autoretry_for=(httpx.HTTPError, ConnectionError, TimeoutError),
    retry_backoff=True,  # exponential: 1, 2, 4, 8, 16... seconds
    retry_backoff_max=600,  # cap each retry delay at 10 min
    retry_jitter=True,  # ±50% jitter to avoid thundering herd
    max_retries=5,  # 5 retries span up to ~25 minutes total
    soft_time_limit=3600,  # 1 hour max
)
def execute_campaign_task(self, campaign_id: str, store_id: str) -> dict:
    """Execute a WhatsApp campaign — send template message to all recipients.

    Retry/DLQ behavior (FR-031..FR-033 / US6):
    - Transient httpx errors and HTTP 429/5xx → autoretry with
      exponential backoff (up to ~25 min over 5 attempts).
    - ``NonRetriableWhatsAppError`` → short-circuit to DLQ writeback,
      no retries.
    - Retries exhausted → final attempt writes a DLQ row before raising.
    """
    import httpx

    from src.core.services.whatsapp_error_classification import (
        NonRetriableWhatsAppError,
    )

    try:
        return _run_async(_execute(campaign_id, store_id))
    except NonRetriableWhatsAppError as exc:
        # Non-retriable — straight to DLQ, no retries.
        logger.warning(
            "campaign_non_retriable_error",
            campaign_id=campaign_id,
            classification=exc.classification,
            code=exc.code,
        )
        try:
            _run_async(_mark_failed(campaign_id))
            _run_async(
                _write_dlq_for_campaign(
                    campaign_id, store_id, exc, classification=exc.classification
                )
            )
        except Exception:
            logger.exception("campaign_dlq_writeback_failed")
        raise  # surfaces to Celery as a final failure; no retry
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        # Retriable: Celery's retry machinery decides whether to retry
        # again or move to the exhausted path.
        if self.request.retries >= self.max_retries:
            logger.error(
                "campaign_retries_exhausted",
                campaign_id=campaign_id,
                retries=self.request.retries,
                error=str(exc),
            )
            try:
                _run_async(_mark_failed(campaign_id))
                _run_async(
                    _write_dlq_for_campaign(
                        campaign_id,
                        store_id,
                        exc,
                        classification="retriable_exhausted",
                    )
                )
            except Exception:
                logger.exception("campaign_dlq_writeback_failed")
            raise
        # Let Celery's autoretry_for handle the actual retry scheduling.
        raise
    except Exception as exc:
        # Unknown error class — treat as retriable but log loudly.
        logger.error(
            "campaign_unknown_error",
            campaign_id=campaign_id,
            error=str(exc),
            exc_info=True,
        )
        try:
            _run_async(_mark_failed(campaign_id))
        except Exception:
            pass
        raise self.retry(exc=exc)


async def _write_dlq_for_campaign(
    campaign_id: str,
    store_id: str,
    exc: Exception,
    *,
    classification: str,
) -> None:
    """Write a single dead-letter row representing a failed campaign
    execution. Per-recipient DLQ rows would explode the table; one
    aggregate row keyed on the campaign keeps the audit tractable.
    """
    from uuid import UUID

    import httpx
    from sqlalchemy import select

    from src.application.use_cases.whatsapp.write_dead_letter import (
        build_error_history_entry,
        write_dead_letter,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.whatsapp_campaign import (
        WhatsAppCampaignModel,
    )
    from src.infrastructure.tenancy.rls import RLSBypassContext

    # Resolve tenant_id from the campaign row. Cross-tenant lookup needs
    # the bypass; the actual DLQ insert opens its own session under RLS.
    async with AsyncSessionLocal() as session:
        async with RLSBypassContext(session):
            row = (
                await session.execute(
                    select(WhatsAppCampaignModel).where(
                        WhatsAppCampaignModel.id == UUID(campaign_id)
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            tenant_id = row.tenant_id

    http_status = None
    meta_code = None
    if isinstance(exc, httpx.HTTPStatusError):
        http_status = exc.response.status_code
    if hasattr(exc, "code"):
        meta_code = getattr(exc, "code", None)

    await write_dead_letter(
        tenant_id=tenant_id,
        store_id=UUID(store_id),
        phone="+0",  # campaign-level DLQ: not a single phone
        originating_context="campaign",
        originating_context_id=UUID(campaign_id),
        error_classification=classification,
        error_history=[
            build_error_history_entry(
                attempt_n=1,
                http_status=http_status,
                meta_error_code=meta_code,
                error_message=str(exc),
            )
        ],
        final_error_code=meta_code,
    )


async def _execute(campaign_id: str, store_id: str) -> dict:
    """Core campaign execution logic."""
    import asyncio as aio

    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.whatsapp_campaign import (
        WhatsAppCampaignModel,
        WhatsAppCampaignRecipientModel,
    )
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service

    cid = UUID(campaign_id)
    sid = UUID(store_id)

    async with AsyncSessionLocal() as session:
        # Load campaign + template
        campaign = (
            await session.execute(
                select(WhatsAppCampaignModel).where(WhatsAppCampaignModel.id == cid)
            )
        ).scalar_one_or_none()

        if not campaign:
            return {"error": "campaign_not_found"}

        if campaign.status not in ("sending", "scheduled"):
            return {"error": f"unexpected_status:{campaign.status}"}

        # Mark as sending
        campaign.status = "sending"
        campaign.started_at = datetime.now(UTC)
        await session.flush()

        template = None
        if campaign.template_id:
            template = (
                await session.execute(
                    select(WhatsAppTemplateModel).where(
                        WhatsAppTemplateModel.id == campaign.template_id
                    )
                )
            ).scalar_one_or_none()

        if not template:
            campaign.status = "failed"
            await session.commit()
            return {"error": "template_not_found"}

        # Get WhatsApp service for this store
        wa_service = await get_whatsapp_service(sid, session)

        # Process recipients in batches
        batch_size = 50
        sent = 0
        failed = 0
        offset = 0

        while True:
            result = await session.execute(
                select(WhatsAppCampaignRecipientModel)
                .where(
                    WhatsAppCampaignRecipientModel.campaign_id == cid,
                    WhatsAppCampaignRecipientModel.status == "pending",
                )
                .limit(batch_size)
            )
            recipients = list(result.scalars().all())
            if not recipients:
                break

            for recipient in recipients:
                try:
                    from src.core.interfaces.services.messaging_service import (
                        MessageContent,
                        MessageRecipient,
                        MessageType,
                    )

                    msg_recipient = MessageRecipient(
                        phone=recipient.phone,
                        name=recipient.customer_name or "Customer",
                    )
                    params = campaign.template_params or {}
                    params.setdefault(
                        "customer_name", recipient.customer_name or "Customer"
                    )

                    content = MessageContent(
                        type=MessageType.CUSTOM,
                        recipient=msg_recipient,
                        template_params=params,
                    )
                    send_result = await wa_service.send_message(content)

                    if send_result.success:
                        recipient.status = "sent"
                        recipient.message_id = send_result.message_id
                        recipient.sent_at = datetime.now(UTC)
                        sent += 1
                    else:
                        recipient.status = "failed"
                        failed += 1
                        logger.warning(
                            "Campaign message failed: phone=%s error=%s",
                            recipient.phone,
                            send_result.error_message,
                        )
                except Exception as e:
                    recipient.status = "failed"
                    failed += 1
                    logger.error(
                        "Campaign send error: phone=%s error=%s", recipient.phone, e
                    )

                # Rate limit: ~50 msgs/sec for safety
                await aio.sleep(0.02)

            # Update campaign counters periodically
            campaign.sent_count = sent
            campaign.failed_count = failed
            await session.flush()
            offset += batch_size

        # Mark complete
        campaign.status = "completed"
        campaign.completed_at = datetime.now(UTC)
        campaign.sent_count = sent
        campaign.failed_count = failed
        await session.commit()

        logger.info(
            "Campaign completed: id=%s sent=%d failed=%d",
            campaign_id,
            sent,
            failed,
        )
        return {"sent": sent, "failed": failed, "status": "completed"}


async def _mark_failed(campaign_id: str) -> None:
    from sqlalchemy import update

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.whatsapp_campaign import (
        WhatsAppCampaignModel,
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(WhatsAppCampaignModel)
            .where(WhatsAppCampaignModel.id == UUID(campaign_id))
            .values(status="failed")
        )
        await session.commit()

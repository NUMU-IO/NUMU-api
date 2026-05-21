"""Celery task for executing WhatsApp broadcast campaigns."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

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
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=3600,  # 1 hour max
)
def execute_campaign_task(self, campaign_id: str, store_id: str) -> dict:
    """Execute a WhatsApp campaign — send template message to all recipients.

    Rate-limited to avoid hitting WhatsApp API limits.
    """
    try:
        return _run_async(_execute(campaign_id, store_id))
    except Exception as exc:
        logger.error(
            "Campaign execution failed: campaign=%s error=%s",
            campaign_id,
            exc,
            exc_info=True,
        )
        # Mark campaign as failed
        try:
            _run_async(_mark_failed(campaign_id))
        except Exception:
            pass
        raise self.retry(exc=exc)


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

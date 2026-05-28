"""Polling sync for PENDING template statuses (FR-028 / FR-028a / US5).

The webhook subscription (T093) is the **primary** signal for template
status updates on the platform WABA. The polling sync exists as:
- A **backfill** for missed webhook deliveries (research R1).
- The **primary** signal for BYO stores whose Meta app webhook NUMU
  does not own.

FR-028a: PENDING templates older than 5 minutes are polled at least
every 15 minutes. Worst-case lag from Meta-side status change to local
row update ≤ 15 minutes (polling fallback); typical lag ≤ 1 minute
(webhook).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="numu_api.whatsapp.poll_pending_templates",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=300,
)
def poll_pending_templates_task(self) -> dict[str, int]:
    """Beat-scheduled (every 15 min). Returns stats for observability."""
    try:
        return _run_async(_poll_all_tenants())
    except Exception as exc:
        logger.error("template_poll_dispatcher_failed", error=str(exc), exc_info=True)
        raise self.retry(exc=exc)


async def _poll_all_tenants() -> dict[str, int]:
    """Find PENDING templates older than 5 minutes, group by tenant,
    fan out the polling work per tenant.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )
    from src.infrastructure.tenancy.rls import RLSBypassContext

    stats: dict[str, int] = {"polled": 0, "updated": 0, "failed": 0}
    cutoff = datetime.now(UTC) - timedelta(minutes=5)

    async with AsyncSessionLocal() as session:
        async with RLSBypassContext(session):
            tenant_rows = (
                await session.execute(
                    select(WhatsAppTemplateModel.tenant_id)
                    .where(
                        WhatsAppTemplateModel.status == "PENDING",
                        WhatsAppTemplateModel.submitted_at <= cutoff,
                    )
                    .distinct()
                )
            ).all()
            tenant_ids = [row[0] for row in tenant_rows]

    for tenant_id in tenant_ids:
        try:
            per_tenant = await _poll_for_tenant(tenant_id)
            for k, v in per_tenant.items():
                stats[k] = stats.get(k, 0) + v
        except Exception as exc:
            logger.warning(
                "template_poll_tenant_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
            )

    logger.info("template_poll_done", **stats)
    return stats


async def _poll_for_tenant(tenant_id: Any) -> dict[str, int]:
    """Poll Meta for PENDING templates belonging to this tenant.

    Resolves the Meta credentials (BYO if active, else platform), calls
    Meta's list_templates, then matches by meta_template_id against
    PENDING local rows and applies status transitions.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )
    from src.infrastructure.external_services.meta.whatsapp_client import (
        WhatsAppClient,
    )
    from src.infrastructure.repositories.credential_repository import (
        CredentialRepository,
    )
    from src.infrastructure.tenancy.rls import RLSContext

    stats = {"polled": 0, "updated": 0, "failed": 0}
    cutoff = datetime.now(UTC) - timedelta(minutes=5)

    async with AsyncSessionLocal() as session:
        async with RLSContext(session, tenant_id):
            pending_rows = (
                (
                    await session.execute(
                        select(WhatsAppTemplateModel).where(
                            WhatsAppTemplateModel.status == "PENDING",
                            WhatsAppTemplateModel.submitted_at <= cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not pending_rows:
                return stats
            stats["polled"] = len(pending_rows)

            # Resolve credentials. BYO first; fall back to platform.
            cred_repo = CredentialRepository(session)
            creds = await cred_repo.get_decrypted_credentials(
                tenant_id=tenant_id,
                service_type=ServiceType.WHATSAPP,
                service_name=ServiceName.WHATSAPP_BUSINESS,
            )
            if not creds:
                # Platform-managed: use the platform WABA settings.
                from src.config import settings

                if not (
                    settings.whatsapp_access_token
                    and settings.whatsapp_business_account_id
                ):
                    logger.warning(
                        "template_poll_no_credentials",
                        tenant_id=str(tenant_id),
                    )
                    return stats
                creds = {
                    "access_token": settings.whatsapp_access_token,
                    "waba_id": settings.whatsapp_business_account_id,
                    "phone_number_id": settings.whatsapp_phone_number_id or "",
                }

            client = WhatsAppClient(
                phone_number_id=creds.get("phone_number_id", ""),
                access_token=creds["access_token"],
                waba_id=creds["waba_id"],
            )
            try:
                try:
                    response = await client.list_templates(limit=100)
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "template_poll_meta_failed",
                        tenant_id=str(tenant_id),
                        http_status=exc.response.status_code,
                    )
                    stats["failed"] += len(pending_rows)
                    return stats
            finally:
                await client.close()

            meta_templates = response.get("data", []) if response else []
            by_id = {str(t.get("id")): t for t in meta_templates if t.get("id")}

            for row in pending_rows:
                if not row.meta_template_id:
                    continue
                meta = by_id.get(str(row.meta_template_id))
                if meta is None:
                    continue
                new_status = (meta.get("status") or "PENDING").upper()
                if new_status == row.status:
                    continue
                row.status = new_status
                if new_status == "REJECTED":
                    row.rejection_reason = meta.get("reason") or meta.get(
                        "rejected_reason"
                    )
                elif new_status == "APPROVED" and row.approved_at is None:
                    row.approved_at = datetime.now(UTC)
                stats["updated"] += 1
                logger.info(
                    "template_status_updated",
                    template_id=str(row.id),
                    meta_template_id=row.meta_template_id,
                    new_status=new_status,
                    source="polling",
                )

            await session.commit()
    return stats

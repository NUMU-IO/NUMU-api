"""WhatsApp broadcast campaign routes."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.stores.whatsapp import (
    AudienceEstimate,
    AudienceFilter,
    CampaignCreate,
    CampaignListResponse,
    CampaignRecipientResponse,
    CampaignRecipientsListResponse,
    CampaignResponse,
    CampaignScheduleRequest,
    CampaignUpdate,
)
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.whatsapp_campaign import (
    WhatsAppCampaignModel,
    WhatsAppCampaignRecipientModel,
)
from src.infrastructure.repositories.whatsapp_campaign_repository import (
    WhatsAppCampaignRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/whatsapp/campaigns")


def _model_to_response(m: WhatsAppCampaignModel) -> CampaignResponse:
    return CampaignResponse(
        id=m.id,
        store_id=m.store_id,
        name=m.name,
        template_id=m.template_id,
        audience_filter=m.audience_filter or {},
        status=m.status,
        scheduled_at=m.scheduled_at,
        started_at=m.started_at,
        completed_at=m.completed_at,
        total_recipients=m.total_recipients,
        sent_count=m.sent_count,
        delivered_count=m.delivered_count,
        read_count=m.read_count,
        failed_count=m.failed_count,
        created_by=m.created_by,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get(
    "",
    response_model=SuccessResponse[CampaignListResponse],
    summary="List campaigns",
    operation_id="list_whatsapp_campaigns",
)
async def list_campaigns(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
    campaign_status: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    repo = WhatsAppCampaignRepository(db)
    campaigns, total = await repo.list_by_store(
        store.id, status=campaign_status, skip=skip, limit=limit
    )
    return SuccessResponse(
        data=CampaignListResponse(
            campaigns=[_model_to_response(c) for c in campaigns],
            total=total,
        ),
        message="Campaigns retrieved",
    )


@router.post(
    "",
    response_model=SuccessResponse[CampaignResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create campaign draft",
    operation_id="create_whatsapp_campaign",
)
async def create_campaign(
    request: CampaignCreate,
    store: Annotated[Store, Depends(get_current_store)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_db),
):
    # Verify template exists and is approved
    from src.infrastructure.repositories.whatsapp_template_repository import (
        WhatsAppTemplateRepository,
    )

    tmpl_repo = WhatsAppTemplateRepository(db)
    tmpl = await tmpl_repo.get_by_id(request.template_id)
    if not tmpl or tmpl.store_id != store.id:
        raise HTTPException(status_code=404, detail="Template not found")
    if tmpl.status != "APPROVED":
        raise HTTPException(
            status_code=400, detail="Template must be APPROVED to use in campaigns"
        )

    repo = WhatsAppCampaignRepository(db)
    model = WhatsAppCampaignModel(
        store_id=store.id,
        tenant_id=store.tenant_id,
        name=request.name,
        template_id=request.template_id,
        audience_filter=request.audience_filter.model_dump()
        if request.audience_filter
        else None,
        template_params=request.template_params,
        status="draft",
        created_by=user_id,
    )
    created = await repo.create(model)
    return SuccessResponse(
        data=_model_to_response(created),
        message="Campaign draft created",
    )


@router.get(
    "/{campaign_id}",
    response_model=SuccessResponse[CampaignResponse],
    summary="Get campaign details",
    operation_id="get_whatsapp_campaign",
)
async def get_campaign(
    campaign_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    repo = WhatsAppCampaignRepository(db)
    campaign = await repo.get_by_id(campaign_id)
    if not campaign or campaign.store_id != store.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return SuccessResponse(
        data=_model_to_response(campaign),
        message="Campaign retrieved",
    )


@router.patch(
    "/{campaign_id}",
    response_model=SuccessResponse[CampaignResponse],
    summary="Update campaign draft",
    operation_id="update_whatsapp_campaign",
)
async def update_campaign(
    campaign_id: UUID,
    request: CampaignUpdate,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    repo = WhatsAppCampaignRepository(db)
    campaign = await repo.get_by_id(campaign_id)
    if not campaign or campaign.store_id != store.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "draft":
        raise HTTPException(status_code=400, detail="Can only edit draft campaigns")

    if request.name is not None:
        campaign.name = request.name
    if request.template_id is not None:
        campaign.template_id = request.template_id
    if request.audience_filter is not None:
        campaign.audience_filter = request.audience_filter.model_dump()
    if request.template_params is not None:
        campaign.template_params = request.template_params
    await db.flush()
    await db.refresh(campaign)

    return SuccessResponse(
        data=_model_to_response(campaign),
        message="Campaign updated",
    )


@router.post(
    "/{campaign_id}/send",
    response_model=SuccessResponse[CampaignResponse],
    summary="Send campaign now",
    operation_id="send_whatsapp_campaign",
)
async def send_campaign(
    campaign_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    """Resolve audience, create recipients, and queue for sending."""
    repo = WhatsAppCampaignRepository(db)
    campaign = await repo.get_by_id(campaign_id)
    if not campaign or campaign.store_id != store.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status not in ("draft", "scheduled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot send campaign in '{campaign.status}' status",
        )

    # Resolve audience
    audience = await _resolve_audience(db, store.id, campaign.audience_filter or {})
    if not audience:
        raise HTTPException(
            status_code=400, detail="No recipients match the audience filter"
        )

    # Create recipient records
    recipients = []
    for cust in audience:
        recipients.append(
            WhatsAppCampaignRecipientModel(
                campaign_id=campaign.id,
                customer_id=cust["id"],
                phone=cust["phone"],
                customer_name=cust["name"],
                status="pending",
            )
        )
    await repo.add_recipients_bulk(recipients)

    # Update campaign
    campaign.status = "sending"
    campaign.total_recipients = len(recipients)
    campaign.started_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(campaign)

    # Queue Celery task
    from src.infrastructure.messaging.tasks.whatsapp_campaign_tasks import (
        execute_campaign_task,
    )

    execute_campaign_task.delay(str(campaign.id), str(store.id))

    return SuccessResponse(
        data=_model_to_response(campaign),
        message=f"Campaign sending to {len(recipients)} recipients",
    )


@router.post(
    "/{campaign_id}/schedule",
    response_model=SuccessResponse[CampaignResponse],
    summary="Schedule campaign",
    operation_id="schedule_whatsapp_campaign",
)
async def schedule_campaign(
    campaign_id: UUID,
    request: CampaignScheduleRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    repo = WhatsAppCampaignRepository(db)
    campaign = await repo.get_by_id(campaign_id)
    if not campaign or campaign.store_id != store.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "draft":
        raise HTTPException(status_code=400, detail="Can only schedule draft campaigns")

    campaign.status = "scheduled"
    campaign.scheduled_at = request.scheduled_at
    await db.flush()
    await db.refresh(campaign)

    # Queue Celery task with ETA
    from src.infrastructure.messaging.tasks.whatsapp_campaign_tasks import (
        execute_campaign_task,
    )

    execute_campaign_task.apply_async(
        args=[str(campaign.id), str(store.id)],
        eta=request.scheduled_at,
    )

    return SuccessResponse(
        data=_model_to_response(campaign),
        message=f"Campaign scheduled for {request.scheduled_at.isoformat()}",
    )


@router.post(
    "/{campaign_id}/cancel",
    response_model=SuccessResponse[CampaignResponse],
    summary="Cancel scheduled campaign",
    operation_id="cancel_whatsapp_campaign",
)
async def cancel_campaign(
    campaign_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    repo = WhatsAppCampaignRepository(db)
    campaign = await repo.get_by_id(campaign_id)
    if not campaign or campaign.store_id != store.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status not in ("draft", "scheduled"):
        raise HTTPException(
            status_code=400, detail="Can only cancel draft or scheduled campaigns"
        )

    campaign.status = "cancelled"
    await db.flush()
    await db.refresh(campaign)

    return SuccessResponse(
        data=_model_to_response(campaign),
        message="Campaign cancelled",
    )


@router.get(
    "/{campaign_id}/recipients",
    response_model=SuccessResponse[CampaignRecipientsListResponse],
    summary="List campaign recipients",
    operation_id="list_campaign_recipients",
)
async def list_recipients(
    campaign_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
    recipient_status: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    repo = WhatsAppCampaignRepository(db)
    campaign = await repo.get_by_id(campaign_id)
    if not campaign or campaign.store_id != store.id:
        raise HTTPException(status_code=404, detail="Campaign not found")

    recipients, total = await repo.list_recipients(
        campaign_id, status=recipient_status, skip=skip, limit=limit
    )
    return SuccessResponse(
        data=CampaignRecipientsListResponse(
            recipients=[
                CampaignRecipientResponse(
                    customer_id=r.customer_id,
                    phone=r.phone,
                    customer_name=r.customer_name,
                    status=r.status,
                    message_id=r.message_id,
                    sent_at=r.sent_at,
                )
                for r in recipients
            ],
            total=total,
        ),
        message="Recipients retrieved",
    )


@router.post(
    "/estimate-audience",
    response_model=SuccessResponse[AudienceEstimate],
    summary="Estimate audience size",
    operation_id="estimate_whatsapp_audience",
)
async def estimate_audience(
    request: AudienceFilter,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    audience = await _resolve_audience(db, store.id, request.model_dump(), limit=5)
    # Get total count without limit
    total = await _count_audience(db, store.id, request.model_dump())
    return SuccessResponse(
        data=AudienceEstimate(
            estimated_count=total,
            sample_recipients=[
                {"name": a["name"], "phone": a["phone"]} for a in audience[:5]
            ],
        ),
        message="Audience estimated",
    )


# ── Helpers ──


async def _resolve_audience(
    db: AsyncSession,
    store_id: UUID,
    filters: dict,
    limit: int | None = None,
) -> list[dict]:
    """Resolve customers matching audience filter."""
    query = select(
        CustomerModel.id,
        CustomerModel.first_name,
        CustomerModel.last_name,
        CustomerModel.phone,
    ).where(
        CustomerModel.store_id == store_id,
        CustomerModel.phone.isnot(None),
        CustomerModel.phone != "",
    )

    if filters.get("ordered_within_days"):
        from src.infrastructure.database.models.tenant.order import OrderModel

        cutoff = datetime.now(UTC) - timedelta(days=filters["ordered_within_days"])
        query = query.where(
            CustomerModel.id.in_(
                select(OrderModel.customer_id)
                .where(OrderModel.store_id == store_id, OrderModel.created_at >= cutoff)
                .distinct()
            )
        )

    if filters.get("inactive_days"):
        from src.infrastructure.database.models.tenant.order import OrderModel

        cutoff = datetime.now(UTC) - timedelta(days=filters["inactive_days"])
        query = query.where(
            ~CustomerModel.id.in_(
                select(OrderModel.customer_id)
                .where(OrderModel.store_id == store_id, OrderModel.created_at >= cutoff)
                .distinct()
            )
        )

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    return [
        {
            "id": row[0],
            "name": f"{row[1] or ''} {row[2] or ''}".strip() or "Customer",
            "phone": row[3],
        }
        for row in result.all()
    ]


async def _count_audience(db: AsyncSession, store_id: UUID, filters: dict) -> int:
    """Count customers matching audience filter."""
    query = select(func.count(CustomerModel.id)).where(
        CustomerModel.store_id == store_id,
        CustomerModel.phone.isnot(None),
        CustomerModel.phone != "",
    )
    result = await db.execute(query)
    return result.scalar() or 0

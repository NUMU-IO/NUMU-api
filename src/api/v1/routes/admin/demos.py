"""Admin demos endpoint.

URL: /api/v1/admin/demos
Requires SUPER_ADMIN role.

Lists every tenant that was ever provisioned via the Try-a-Demo flow,
alongside its current lifecycle state so growth/conversion can be tracked.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)

router = APIRouter()


class DemoRowResponse(BaseModel):
    tenant_id: UUID
    subdomain: str
    demo_email: str | None
    demo_started_at: datetime | None
    expires_at: datetime | None
    lifecycle_state: str
    converted: bool


class DemoStatsResponse(BaseModel):
    total_demos: int
    still_demo: int
    converted_to_trial: int
    converted_to_active: int
    conversion_rate: float


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[DemoRowResponse]],
    summary="List demo tenants with conversion status",
    operation_id="list_demos",
)
async def list_demos(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    filter_state: Literal["all", "demo", "converted"] = Query("all"),
):
    base = select(TenantModel).where(TenantModel.demo_email.isnot(None))
    if filter_state == "demo":
        base = base.where(TenantModel.lifecycle_state == TenantLifecycleState.DEMO)
    elif filter_state == "converted":
        base = base.where(
            TenantModel.lifecycle_state.in_([
                TenantLifecycleState.TRIAL,
                TenantLifecycleState.ACTIVE,
            ])
        )

    total_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(total_stmt)).scalar_one()

    skip = (page - 1) * page_size
    rows_stmt = (
        base.order_by(TenantModel.demo_started_at.desc()).offset(skip).limit(page_size)
    )
    rows = (await db.execute(rows_stmt)).scalars().all()

    def _converted(state: str) -> bool:
        return state in (TenantLifecycleState.TRIAL, TenantLifecycleState.ACTIVE)

    items = [
        DemoRowResponse(
            tenant_id=t.id,
            subdomain=t.subdomain,
            demo_email=t.demo_email,
            demo_started_at=t.demo_started_at,
            expires_at=t.expires_at,
            lifecycle_state=t.lifecycle_state,
            converted=_converted(t.lifecycle_state),
        )
        for t in rows
    ]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
        ),
        message="Demo tenants retrieved",
    )


@router.get(
    "/stats",
    response_model=SuccessResponse[DemoStatsResponse],
    summary="Demo-to-trial conversion statistics",
    operation_id="demo_stats",
)
async def demo_stats(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    base = (
        select(func.count())
        .select_from(TenantModel)
        .where(TenantModel.demo_email.isnot(None))
    )

    total = (await db.execute(base)).scalar_one()
    still_demo = (
        await db.execute(
            base.where(TenantModel.lifecycle_state == TenantLifecycleState.DEMO)
        )
    ).scalar_one()
    to_trial = (
        await db.execute(
            base.where(TenantModel.lifecycle_state == TenantLifecycleState.TRIAL)
        )
    ).scalar_one()
    to_active = (
        await db.execute(
            base.where(TenantModel.lifecycle_state == TenantLifecycleState.ACTIVE)
        )
    ).scalar_one()
    converted = to_trial + to_active

    return SuccessResponse(
        data=DemoStatsResponse(
            total_demos=total,
            still_demo=still_demo,
            converted_to_trial=to_trial,
            converted_to_active=to_active,
            conversion_rate=(converted / total) if total else 0.0,
        ),
        message="Demo conversion stats",
    )

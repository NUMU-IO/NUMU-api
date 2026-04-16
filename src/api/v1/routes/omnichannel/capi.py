"""CAPI events routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.application.dto.omnichannel import SendCapiEventDTO
from src.application.use_cases.omnichannel import SendCapiEventUseCase
from src.infrastructure.repositories import StoreRepository

router = APIRouter(tags=["Omnichannel"])


@router.post("/event", response_model=dict, status_code=status.HTTP_200_OK)
async def send_capi_event(
    dto: SendCapiEventDTO,
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    store_repo: StoreRepository = Depends(get_store_repository),
) -> SuccessResponse:
    """Send a CAPI event to Meta.

    PUT /stores/{store_id}/channels/meta/capi
    Body: { "event_name", "event_id", "event_time", "user_data", "custom_data", "event_source_url" }
    """
    use_case = SendCapiEventUseCase(store_repository=store_repo)
    result = await use_case.execute(
        store_id=dto.store_id,
        event_name=dto.event_name,
        event_id=dto.event_id,
        event_time=dto.event_time,
        user_data=dto.user_data,
        custom_data=dto.custom_data,
        event_source_url=dto.event_source_url,
    )
    return SuccessResponse(
        data=result,
        message="CAPI event sent",
    )


__all__ = ["router"]

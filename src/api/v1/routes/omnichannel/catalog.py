"""Catalog sync routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_catalog_mapping_repository,
    get_channel_connection_repository,
    get_product_repository,
)
from src.api.responses import SuccessResponse
from src.application.dto.omnichannel import SyncCatalogDTO
from src.application.use_cases.omnichannel import SyncCatalogUseCase
from src.infrastructure.repositories import (
    CatalogMappingRepositoryImpl,
    ChannelConnectionRepositoryImpl,
    ProductRepository,
)

router = APIRouter(tags=["Omnichannel"])


@router.post("/sync", response_model=dict, status_code=status.HTTP_200_OK)
async def sync_catalog(
    dto: SyncCatalogDTO,
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    connection_repo: ChannelConnectionRepositoryImpl = Depends(
        get_channel_connection_repository
    ),
    catalog_repo: CatalogMappingRepositoryImpl = Depends(
        get_catalog_mapping_repository
    ),
    product_repo: ProductRepository = Depends(get_product_repository),
) -> SuccessResponse:
    """Trigger catalog sync to Meta.

    POST /stores/{store_id}/channels/meta/catalog/sync
    """
    use_case = SyncCatalogUseCase(
        channel_connection_repository=connection_repo,
        catalog_mapping_repository=catalog_repo,
        product_repository=product_repo,
    )
    result = await use_case.execute(
        connection_id=dto.connection_id,
        store_id=store_id,
        full_sync=dto.full_sync,
    )
    return SuccessResponse(
        data=result,
        message="Catalog sync completed",
    )


@router.get("/mappings", response_model=dict, status_code=status.HTTP_200_OK)
async def list_catalog_mappings(
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    catalog_repo: CatalogMappingRepositoryImpl = Depends(
        get_catalog_mapping_repository
    ),
) -> SuccessResponse:
    """List catalog product mappings."""
    mappings = await catalog_repo.list_by_store(store_id)
    return SuccessResponse(
        data=[
            {
                "id": m.id,
                "product_id": m.product_id,
                "external_product_id": m.external_product_id,
                "sync_status": m.sync_status.value,
            }
            for m in mappings
        ],
        message=None,
    )


__all__ = ["router"]

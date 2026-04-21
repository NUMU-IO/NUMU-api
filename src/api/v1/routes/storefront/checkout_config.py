"""Public storefront checkout-fields config.

URL: GET /storefront/store/{store_id}/checkout-config

The storefront fetches this to render the dynamic checkout form — which
standard fields are enabled/required, plus any custom fields the merchant
has added. No auth.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.core.checkout_fields import resolve_config
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import StoreRepository

router = APIRouter()


@router.get(
    "/checkout-config",
    response_model=SuccessResponse[dict],
    summary="Get public checkout field config",
    operation_id="get_public_checkout_config",
)
async def get_public_checkout_config(
    store_id: Annotated[UUID, Path(description="Store ID")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))
    return SuccessResponse(
        data=resolve_config(store.settings),
        message="Checkout config retrieved",
    )

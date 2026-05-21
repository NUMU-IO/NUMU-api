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
    config = resolve_config(store.settings)

    # When the merchant has cod_trust enabled, phone becomes non-optional
    # at COD checkout — surface this so the storefront form can mark the
    # field required up-front instead of letting the user discover it via
    # a 400 from the checkout endpoint.
    cod_trust = (store.settings or {}).get("cod_trust") or {}
    if isinstance(cod_trust, dict) and cod_trust.get("enabled"):
        std = config.setdefault("standard_fields", {})
        phone_cfg = std.setdefault("phone", {"enabled": True, "required": True})
        phone_cfg["enabled"] = True
        phone_cfg["required"] = True
        # Marker the storefront uses to show a tooltip / explainer.
        phone_cfg["required_reason"] = "cod_trust"

    return SuccessResponse(
        data=config,
        message="Checkout config retrieved",
    )

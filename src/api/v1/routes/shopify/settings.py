"""App settings endpoints — thresholds, integrations."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from src.api.dependencies.shopify import (
    get_shopify_settings_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    AppSettingsResponse,
    ConnectPaymobRequest,
    UpdateSettingsRequest,
)
from src.infrastructure.repositories.shopify_repository import (
    ShopifyAppSettingsRepository,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


def _model_to_response(m) -> AppSettingsResponse:  # noqa: ANN001
    return AppSettingsResponse(
        store_id=str(m.store_id),
        cod_risk_scoring_enabled=m.cod_risk_scoring_enabled,
        auto_approve_threshold=m.auto_approve_threshold,
        auto_hold_threshold=m.auto_hold_threshold,
        auto_cancel_threshold=m.auto_cancel_threshold,
        paymob_connected=m.paymob_connected,
        whatsapp_connected=m.whatsapp_connected,
    )


@router.get(
    "/{store_id}/settings",
    response_model=SuccessResponse[AppSettingsResponse],
    summary="Get app settings",
    operation_id="shopify_get_settings",
)
async def get_settings(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[ShopifyAppSettingsRepository, Depends(get_shopify_settings_repo)],
):
    model = await repo.get_or_create(store_id)
    return SuccessResponse(data=_model_to_response(model))


@router.patch(
    "/{store_id}/settings",
    response_model=SuccessResponse[AppSettingsResponse],
    summary="Update risk thresholds / toggles",
    operation_id="shopify_update_settings",
)
async def update_settings(
    store_id: Annotated[UUID, Path()],
    body: UpdateSettingsRequest,
    repo: Annotated[ShopifyAppSettingsRepository, Depends(get_shopify_settings_repo)],
):
    model = await repo.update(store_id, body.model_dump(exclude_unset=True))
    return SuccessResponse(data=_model_to_response(model), message="Settings updated")


@router.post(
    "/{store_id}/settings/paymob",
    response_model=SuccessResponse[AppSettingsResponse],
    summary="Connect Paymob integration",
    operation_id="shopify_connect_paymob",
)
async def connect_paymob(
    store_id: Annotated[UUID, Path()],
    body: ConnectPaymobRequest,
    repo: Annotated[ShopifyAppSettingsRepository, Depends(get_shopify_settings_repo)],
):
    # In production this would validate the API key against Paymob.
    # For now we just mark paymob_connected = True.
    model = await repo.update(store_id, {"paymob_connected": True})
    return SuccessResponse(data=_model_to_response(model), message="Paymob connected")

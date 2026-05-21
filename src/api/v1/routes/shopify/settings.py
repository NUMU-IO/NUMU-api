"""App settings endpoints — thresholds, integrations."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
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
from src.application.use_cases.shopify.configure_paymob import (
    ConfigureFailure,
    ConfigureSuccess,
    configure_paymob_credentials,
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
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Validate, encrypt, persist, then flip `paymob_connected=true`.

    backend-018: was a stub that flipped the flag without validation.
    Now hits Paymob's intention API with a $0.01 ``is_test=true``
    charge. Failure → 422 with the upstream message; nothing
    persisted, ``paymob_connected`` stays unchanged.
    """
    result = await configure_paymob_credentials(
        session=db,
        store_id=store_id,
        secret_key=body.secret_key,
        public_key=body.public_key,
        hmac_secret=body.hmac_secret,
        card_integration_id=body.card_integration_id,
        wallet_integration_id=body.wallet_integration_id,
    )
    if isinstance(result, ConfigureFailure):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Paymob validation failed: {result.reason}",
        )
    assert isinstance(result, ConfigureSuccess)
    model = await repo.update(store_id, {"paymob_connected": True})
    return SuccessResponse(data=_model_to_response(model), message="Paymob connected")

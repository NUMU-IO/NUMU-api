"""COD protection for apps: one place to read and change a store's COD rules.

URL: /stores/{store_id}/cod/settings (scope ``cod:read`` / ``cod:write``)

The COD rules live in four settings blocks with four merchant routes (Trust
Network, the deposit policy under payment, WhatsApp tap-to-confirm, the
checkout phone OTP). An app like COD Shield manages them together, and
apps never hold ``settings:*``, so this route reads them as one bundle and
writes each section through the merchant route that owns it: the same
validation, the same side effects, one source of truth.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_onboarding_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.stores import settings as settings_routes
from src.api.v1.routes.stores import whatsapp as whatsapp_routes
from src.api.v1.schemas.tenant.settings import (
    CodDepositPolicy,
    UpdateCodTrustRequest,
    UpdatePaymentSettingsRequest,
)
from src.core.checkout_fields import CheckoutFieldsConfig
from src.core.entities.store import Store
from src.infrastructure.repositories import OnboardingRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/cod")


class Confirmation(BaseModel):
    """WhatsApp tap-to-confirm before a COD order ships."""

    require_order_confirmation: bool = False
    delay_minutes: int = 0


class Otp(BaseModel):
    """The phone OTP at checkout. ``available`` is whether this store can
    send one at all (platform switch, WhatsApp access, a GOWA number)."""

    require_verification: bool = True
    available: bool = False


class CodSettings(BaseModel):
    trust: dict
    deposit: CodDepositPolicy
    confirmation: Confirmation
    otp: Otp


class ConfirmationUpdate(BaseModel):
    require_order_confirmation: bool | None = None
    delay_minutes: int | None = Field(default=None, ge=0, le=1440)


class OtpUpdate(BaseModel):
    require_verification: bool


class CodSettingsUpdate(BaseModel):
    """Any subset of the sections; each is validated by its own route."""

    trust: UpdateCodTrustRequest | None = None
    deposit: CodDepositPolicy | None = None
    confirmation: ConfirmationUpdate | None = None
    otp: OtpUpdate | None = None


async def _read(store: Store, db: AsyncSession) -> CodSettings:
    from src.application.services.checkout_identity import otp_available
    from src.core.checkout_fields import resolve_config

    settings = store.settings or {}
    payment = settings_routes._build_payment_response(settings.get("payment", {}))
    notifications = settings.get("whatsapp_notifications") or {}
    identity = resolve_config(settings)["identity"]
    return CodSettings(
        trust=settings_routes._get_cod_trust_settings(settings),
        deposit=payment.cod_deposit_policy,
        confirmation=Confirmation(
            require_order_confirmation=bool(
                notifications.get("require_order_confirmation", False)
            ),
            delay_minutes=int(
                (settings.get("whatsapp") or {}).get("confirm_order_delay_minutes") or 0
            ),
        ),
        otp=Otp(
            require_verification=bool(identity.get("require_verification", True)),
            available=await otp_available(store.id, settings, db),
        ),
    )


@router.get(
    "/settings",
    response_model=SuccessResponse[CodSettings],
    summary="Read the store's COD protection rules",
    operation_id="get_cod_settings",
)
async def get_cod_settings(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return SuccessResponse(data=await _read(store, db))


@router.patch(
    "/settings",
    response_model=SuccessResponse[CodSettings],
    summary="Change the store's COD protection rules",
    operation_id="update_cod_settings",
)
async def update_cod_settings(
    body: CodSettingsUpdate,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Each section goes through the merchant route that owns it, so a
    deposit gateway that isn't configured, or confirmation without
    WhatsApp access, is refused here exactly as it is in the hub."""
    if body.trust is not None:
        await settings_routes.update_cod_trust_settings_endpoint(
            request=body.trust, store=store, store_repo=store_repo
        )
    if body.deposit is not None:
        await settings_routes.update_payment_settings(
            request=UpdatePaymentSettingsRequest(cod_deposit_policy=body.deposit),
            store=store,
            store_repo=store_repo,
            onboarding_repo=onboarding_repo,
        )
    if body.confirmation is not None:
        c = body.confirmation
        if c.require_order_confirmation is not None:
            await whatsapp_routes.byo_update_notifications(
                body={"require_order_confirmation": c.require_order_confirmation},
                store=store,
                db=db,
            )
        if c.delay_minutes is not None:
            await whatsapp_routes.update_whatsapp_settings(
                body=whatsapp_routes.WhatsAppSettingsUpdate(
                    confirm_order_delay_minutes=c.delay_minutes
                ),
                store=store,
                db=db,
            )
    if body.otp is not None:
        from src.core.checkout_fields import resolve_config

        cfg = resolve_config(store.settings or {})
        cfg["identity"] = {
            **cfg["identity"],
            "require_verification": body.otp.require_verification,
        }
        await settings_routes.update_checkout_fields(
            payload=CheckoutFieldsConfig.model_validate(cfg),
            store=store,
            store_repo=store_repo,
        )
    return SuccessResponse(data=await _read(store, db), message="COD settings saved")

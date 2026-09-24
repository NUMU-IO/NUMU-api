"""Partner App usage charges: ``POST /api/v1/app/usage-charges``.

Authenticated with the app's own token (``Authorization: Bearer numu_app_…``).
The installation, and so the store, comes from the token: an app can only
charge the store that installed it, within the cap the merchant approved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from src.api.responses import SuccessResponse
from src.application.services.app_billing import (
    InsufficientFundsError,
    UsageError,
    WalletChargeSource,
    record_usage,
)
from src.application.services.app_tokens import APP_TOKEN_PREFIX, resolve_app_token
from src.application.services.notification_feed import emit_notification_standalone
from src.application.services.wallet_service import WalletSuspendedError
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal

logger = get_logger(__name__)

router = APIRouter(prefix="/app", tags=["App usage"])


class UsageChargeIn(BaseModel):
    #: Piasters, for an app priced by amount.
    amount_cents: int | None = Field(default=None, ge=1, le=10_000_000)
    #: For an app priced per unit (``pricing.usage.price_cents``).
    units: int | None = Field(default=None, ge=1, le=1_000_000)
    description: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=100)


class UsageChargeOut(BaseModel):
    id: UUID
    amount_cents: int
    units: int | None
    description: str
    idempotency_key: str
    period_start: datetime
    created_at: datetime | None


@router.post(
    "/usage-charges",
    response_model=SuccessResponse[UsageChargeOut],
    status_code=status.HTTP_201_CREATED,
    operation_id="create_app_usage_charge",
)
async def create_usage_charge(
    body: UsageChargeIn,
    authorization: Annotated[str | None, Header()] = None,
):
    """Charge the store's wallet for usage now. Replaying an
    ``idempotency_key`` returns the first charge. 402 when the subscription
    is not active or the wallet can't pay; 422 over the approved cap."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    notices: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as session:
        principal = (
            await resolve_app_token(session, token)
            if token.startswith(APP_TOKEN_PREFIX)
            else None
        )
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked app token",
            )
        source = WalletChargeSource(session)
        try:
            record, new = await record_usage(
                session,
                installation=principal.installation,
                app=principal.app,
                source=source,
                amount_cents=body.amount_cents,
                units=body.units,
                description=body.description,
                idempotency_key=body.idempotency_key,
                notices=notices,
            )
        except UsageError as exc:
            await session.rollback()
            for n in notices:
                await emit_notification_standalone(**n)
            code = (
                status.HTTP_402_PAYMENT_REQUIRED
                if exc.code == "subscription_inactive"
                else 422
            )
            raise HTTPException(
                status_code=code, detail={"code": exc.code, "message": str(exc)}
            ) from None
        except (InsufficientFundsError, WalletSuspendedError):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "insufficient_wallet_balance",
                    "message": "The store's wallet can't cover this charge.",
                },
            ) from None
        out = UsageChargeOut.model_validate(record, from_attributes=True)
        await session.commit()
        await source.invalidate()
    for n in notices:
        await emit_notification_standalone(**n)
    if new:
        logger.info(
            "app_usage_charged",
            app=principal.app.slug,
            store_id=str(principal.installation.store_id),
            amount_cents=out.amount_cents,
        )
    return SuccessResponse(data=out)

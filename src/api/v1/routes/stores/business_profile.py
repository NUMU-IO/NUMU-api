"""Merchant-facing commercial details: registration, tax id, payout account.

Collect and surface. No endpoint here blocks anything — a merchant with
an empty profile sells exactly as before. The fields exist so we know who
could take non-COD money and issue a compliant invoice, and so a future
gate (gateway KYC, the CBE posture on pay-as-you-go, ZATCA when Saudi
lands) has a record to read instead of a migration to invent.

The payout account number is write-only over this API. It goes in
encrypted and comes back as a masked tail; there is no endpoint that
returns it. Nothing legitimate needs to read a merchant's account number
back to them in a browser.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.store import Store

router = APIRouter()


class BusinessProfileResponse(BaseModel):
    """What the hub renders. Never includes the account number."""

    is_registered_business: bool | None = None
    tax_id: str | None = None
    payout_bank_name: str | None = None
    payout_account_name: str | None = None
    # e.g. "•••• 4471". Present iff an account number has been stored.
    payout_masked: str | None = None
    has_payout_account: bool = False
    is_complete: bool = False


class BusinessProfileRequest(BaseModel):
    """Every field optional — omitted means "leave alone", not "clear"."""

    is_registered_business: bool | None = None
    tax_id: str | None = Field(None, max_length=50)
    payout_bank_name: str | None = Field(None, max_length=120)
    payout_account_name: str | None = Field(None, max_length=160)
    payout_account_number: str | None = Field(
        None,
        max_length=64,
        description="Write-only. Stored encrypted; never returned.",
    )


def _to_response(profile) -> BusinessProfileResponse:
    if profile is None:
        return BusinessProfileResponse()
    return BusinessProfileResponse(
        is_registered_business=profile.is_registered_business,
        tax_id=profile.tax_id,
        payout_bank_name=profile.payout_bank_name,
        payout_account_name=profile.payout_account_name,
        payout_masked=profile.payout_masked,
        has_payout_account=profile.has_payout_account,
        is_complete=profile.is_complete,
    )


@router.get(
    "/business-profile",
    response_model=SuccessResponse[BusinessProfileResponse],
    summary="Get commercial details",
    operation_id="get_business_profile",
)
async def get_business_profile(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Read the tenant's commercial details. Empty profile when never set."""
    from src.application.services.business_profile import get_profile

    profile = await get_profile(db, tenant_id=store.tenant_id)
    return SuccessResponse(data=_to_response(profile), message="OK")


@router.put(
    "/business-profile",
    response_model=SuccessResponse[BusinessProfileResponse],
    summary="Update commercial details",
    operation_id="update_business_profile",
)
async def update_business_profile(
    request: BusinessProfileRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Write the parts that were supplied. Nothing here gates selling."""
    from src.application.services.business_profile import upsert_profile

    profile = await upsert_profile(
        db,
        tenant_id=store.tenant_id,
        is_registered_business=request.is_registered_business,
        tax_id=request.tax_id,
        payout_bank_name=request.payout_bank_name,
        payout_account_name=request.payout_account_name,
        payout_account_number=request.payout_account_number,
    )
    await db.commit()
    return SuccessResponse(data=_to_response(profile), message="Saved")

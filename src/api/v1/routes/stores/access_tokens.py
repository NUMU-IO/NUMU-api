"""Personal Access Token routes nested under stores.

URL: /stores/{store_id}/access-tokens

Lets a store owner mint long-lived API tokens for machine clients (the NUMU
MCP server, n8n, scripts, …). A token inherits the owner's permissions and is
scoped to the store's tenant. The raw token is returned exactly once at
creation; thereafter only metadata is exposed.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.personal_access_token_service import (
    PersonalAccessTokenService,
)
from src.core.entities.store import Store
from src.infrastructure.database.models.public.personal_access_token import (
    PersonalAccessTokenModel,
)

router = APIRouter(prefix="/{store_id}/access-tokens")


class CreateAccessTokenRequest(BaseModel):
    """Request body for minting a personal access token."""

    name: str = Field(
        min_length=1,
        max_length=100,
        description="Human-friendly label, e.g. 'Claude MCP'.",
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Optional lifetime in days. Omit for a non-expiring token.",
    )


class AccessTokenResponse(BaseModel):
    """Metadata for a personal access token (never includes the secret)."""

    id: str
    name: str
    token_prefix: str
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None
    created_at: str


class CreatedAccessTokenResponse(AccessTokenResponse):
    """Returned once at creation — carries the raw secret a single time."""

    token: str = Field(description="The secret token. Shown once; store it now.")


def _to_response(record: PersonalAccessTokenModel) -> AccessTokenResponse:
    return AccessTokenResponse(
        id=str(record.id),
        name=record.name,
        token_prefix=record.token_prefix,
        last_used_at=record.last_used_at.isoformat() if record.last_used_at else None,
        expires_at=record.expires_at.isoformat() if record.expires_at else None,
        revoked_at=record.revoked_at.isoformat() if record.revoked_at else None,
        created_at=record.created_at.isoformat(),
    )


@router.post(
    "/",
    response_model=SuccessResponse[CreatedAccessTokenResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a personal access token",
    operation_id="create_access_token",
)
async def create_access_token(
    request: CreateAccessTokenRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Mint a long-lived API token for this store. The secret is returned once."""
    if store.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store is not associated with a tenant",
        )

    expires_at = (
        datetime.now(UTC) + timedelta(days=request.expires_in_days)
        if request.expires_in_days
        else None
    )

    service = PersonalAccessTokenService(db)
    raw, record = await service.create(
        user_id=store.owner_id,
        tenant_id=store.tenant_id,
        store_id=store.id,
        name=request.name,
        expires_at=expires_at,
    )

    base = _to_response(record)
    return SuccessResponse(
        data=CreatedAccessTokenResponse(**base.model_dump(), token=raw),
        message="Access token created. Copy it now — it won't be shown again.",
    )


@router.get(
    "/",
    response_model=SuccessResponse[list[AccessTokenResponse]],
    summary="List personal access tokens",
    operation_id="list_access_tokens",
)
async def list_access_tokens(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List this owner's tokens for the store's tenant (secrets excluded)."""
    if store.tenant_id is None:
        return SuccessResponse(data=[])

    service = PersonalAccessTokenService(db)
    records = await service.list_for(user_id=store.owner_id, tenant_id=store.tenant_id)
    return SuccessResponse(data=[_to_response(r) for r in records])


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a personal access token",
    operation_id="revoke_access_token",
)
async def revoke_access_token(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    token_id: Annotated[UUID, Path(description="The token id to revoke")],
):
    """Permanently revoke a token. Idempotent; safe to call on an already-revoked id."""
    service = PersonalAccessTokenService(db)
    found = await service.revoke(token_id=token_id, user_id=store.owner_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access token not found",
        )

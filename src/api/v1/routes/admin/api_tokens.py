"""Admin view of merchant API tokens and what each one is doing (SUPER_ADMIN).

Lists every personal access token with who minted it and for which store,
plus a summary of its recent request trail, and returns the trail itself for
one token. Read-only: revoking stays with the merchant, or with the per-tenant
API-access switch on the merchant page.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.middleware.token_activity import KEEP, recent_requests
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.personal_access_token import (
    PersonalAccessTokenModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel

router = APIRouter(
    prefix="/api-tokens",
    tags=["Admin - API tokens"],
    dependencies=[Depends(require_admin)],
)

REFUSED = {401, 402, 403}


@router.get("", response_model=SuccessResponse[list[dict]])
async def list_api_tokens(db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (
        await db.execute(
            select(
                PersonalAccessTokenModel,
                TenantModel.subdomain,
                StoreModel.name,
                UserModel.email,
            )
            .outerjoin(
                TenantModel, TenantModel.id == PersonalAccessTokenModel.tenant_id
            )
            .outerjoin(StoreModel, StoreModel.id == PersonalAccessTokenModel.store_id)
            .outerjoin(UserModel, UserModel.id == PersonalAccessTokenModel.user_id)
            .order_by(PersonalAccessTokenModel.last_used_at.desc().nullslast())
        )
    ).all()

    items = []
    for token, subdomain, store_name, email in rows:
        trail = await recent_requests(str(token.id))
        items.append({
            "id": str(token.id),
            "name": token.name,
            "token_prefix": token.token_prefix,
            "scopes": token.scopes,
            "tenant": subdomain,
            "store_id": str(token.store_id) if token.store_id else None,
            "store_name": store_name,
            "minted_by": email,
            "created_at": token.created_at,
            "last_used_at": token.last_used_at,
            "expires_at": token.expires_at,
            "revoked_at": token.revoked_at,
            "trail": {
                "requests": len(trail),
                "refused": sum(1 for e in trail if e["status"] in REFUSED),
                "distinct_ips": len({e["ip"] for e in trail}),
                "last": trail[0] if trail else None,
            },
        })
    return SuccessResponse(data=items)


@router.get("/{token_id}/requests", response_model=SuccessResponse[list[dict]])
async def list_api_token_requests(
    token_id: UUID,
    limit: Annotated[int, Query(ge=1, le=KEEP)] = 200,
):
    return SuccessResponse(data=await recent_requests(str(token_id), limit))

"""Public marketplace catalog routes.

Catalog can be read anonymously (returns themes with
``flags.catalog_visible = true`` only). When the request has a valid
access token, the user's UUID is also passed to the service so the
``flags.visible_to_user_ids`` internal allowlist and
``flags.visible_to_pct`` percentage gate can take effect. Anonymous
requests skip both gates and see only fully-public themes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.dependencies.repositories import get_marketplace_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    CatalogListResponse,
    ThemeDetailResponse,
)
from src.application.services.marketplace_service import MarketplaceService
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.infrastructure.external_services.token_service import token_service
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

router = APIRouter(prefix="/marketplace/catalog", tags=["Marketplace Catalog"])


def _svc(
    repo: Annotated[MarketplaceRepository, Depends(get_marketplace_repository)],
) -> MarketplaceService:
    return MarketplaceService(marketplace_repo=repo)


async def _maybe_user_id(request: Request) -> str | None:
    """Best-effort: read the user id from a valid token if present,
    else None. Never raises — the catalog endpoint must work for
    anonymous visitors. Used to apply the per-user visibility flags."""
    auth = request.headers.get("authorization") or ""
    token: str | None = None
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    token = token or request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = token_service.verify_token(token)
    except (TokenExpiredError, InvalidTokenError):
        return None
    return str(payload.user_id)


@router.get("/themes", response_model=SuccessResponse[CatalogListResponse])
async def browse_themes(
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[str | None, Depends(_maybe_user_id)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
):
    """Browse published marketplace themes."""
    data = await svc.browse_themes(
        page=page, per_page=per_page, category=category, user_id=user_id
    )
    return SuccessResponse(data=CatalogListResponse(**data))


@router.get("/themes/{slug}", response_model=SuccessResponse[ThemeDetailResponse])
async def get_theme_detail(
    slug: str,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """Get detailed information about a published marketplace theme."""
    try:
        data = await svc.get_theme_detail(slug)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=ThemeDetailResponse(**data))

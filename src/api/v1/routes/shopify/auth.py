"""Shopify auth endpoints — register-shop and lookup."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies.shopify import (
    get_shopify_installation_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    InstallationResponse,
    LookupResponse,
    RegisterShopRequest,
)
from src.infrastructure.repositories.shopify_repository import (
    ShopifyInstallationRepository,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


@router.post(
    "/register-shop",
    response_model=SuccessResponse[InstallationResponse],
    status_code=status.HTTP_200_OK,
    summary="Register or re-register a Shopify store on app install",
    operation_id="register_shop",
)
async def register_shop(
    request: RegisterShopRequest,
    repo: Annotated[ShopifyInstallationRepository, Depends(get_shopify_installation_repo)],
):
    """Called by the Shopify app's afterAuth hook.

    If the domain already exists the tokens are updated; otherwise a new
    installation record is created with a fresh store_id and tenant_id.
    """
    existing = await repo.get_by_domain(request.shopify_domain)

    if existing:
        # Update existing installation
        existing.access_token_encrypted = request.access_token
        existing.scopes = request.scopes
        existing.is_active = True
        existing.uninstalled_at = None

        from sqlalchemy.ext.asyncio import AsyncSession
        # flush is handled by upsert, but we already have the model
        return SuccessResponse(
            data=InstallationResponse(
                store_id=str(existing.store_id),
                tenant_id=str(existing.tenant_id) if existing.tenant_id else "",
                shopify_domain=existing.shopify_domain,
                status="active",
                app_plan=existing.app_plan,
            ),
            message="Shop re-registered successfully",
        )

    # Brand-new install — create a placeholder store_id.
    # tenant_id is left as None until the merchant links a Numu tenant.
    store_id = uuid4()

    installation = await repo.upsert(
        store_id=store_id,
        tenant_id=None,
        shopify_domain=request.shopify_domain,
        access_token_encrypted=request.access_token,
        scopes=request.scopes,
    )

    return SuccessResponse(
        data=InstallationResponse(
            store_id=str(installation.store_id),
            tenant_id=str(installation.tenant_id) if installation.tenant_id else "",
            shopify_domain=installation.shopify_domain,
            status="active",
            app_plan=installation.app_plan,
        ),
        message="Shop registered successfully",
    )


@router.get(
    "/lookup",
    response_model=SuccessResponse[LookupResponse],
    summary="Lookup store_id by Shopify domain",
    operation_id="lookup_shop",
)
async def lookup_shop(
    domain: Annotated[str, Query(description="e.g. example.myshopify.com")],
    repo: Annotated[ShopifyInstallationRepository, Depends(get_shopify_installation_repo)],
):
    installation = await repo.get_by_domain(domain)
    if not installation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active installation found for domain: {domain}",
        )
    return SuccessResponse(
        data=LookupResponse(store_id=str(installation.store_id)),
    )

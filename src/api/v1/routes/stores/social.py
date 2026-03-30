"""Social import routes nested under stores.

URL: /stores/{store_id}/social
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_product_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.social import (
    ConnectSocialRequest,
    ImportFromUrlRequest,
    ImportFromUrlResponse,
    ImportPostsRequest,
    ImportPostsResponse,
    SocialConnectionResponse,
    SocialPostResponse,
    SocialPostsListResponse,
    UrlImportResultResponse,
)
from src.core.entities.social_connection import SocialPlatform
from src.core.entities.store import Store
from src.infrastructure.external_services.meta import MetaSocialService
from src.infrastructure.repositories.social_connection_repository import (
    SocialConnectionRepository,
)
from src.infrastructure.repositories.social_post_repository import (
    SocialPostRepository,
)

router = APIRouter(prefix="/{store_id}/social")


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _get_meta_service() -> MetaSocialService:
    return MetaSocialService()


def _get_social_connection_repo(
    session=Depends(get_db),
) -> SocialConnectionRepository:
    return SocialConnectionRepository(session)


def _get_social_post_repo(
    session=Depends(get_db),
) -> SocialPostRepository:
    return SocialPostRepository(session)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connection_response(conn) -> SocialConnectionResponse:
    return SocialConnectionResponse(
        id=str(conn.id),
        platform=conn.platform
        if isinstance(conn.platform, str)
        else conn.platform.value,
        handle=conn.handle,
        followers=conn.followers,
        posts_count=conn.posts_count,
        status=conn.status if isinstance(conn.status, str) else conn.status.value,
        last_synced_at=str(conn.last_synced_at) if conn.last_synced_at else None,
    )


# ---------------------------------------------------------------------------
# US1 — Connect / Disconnect Social Accounts
# ---------------------------------------------------------------------------


@router.get(
    "/connections",
    response_model=SuccessResponse[list[SocialConnectionResponse]],
    summary="List social connections",
    operation_id="list_social_connections",
)
async def list_connections(
    store: Annotated[Store, Depends(verify_store_ownership)],
    conn_repo: Annotated[
        SocialConnectionRepository, Depends(_get_social_connection_repo)
    ],
):
    """List all active social connections for this store."""
    connections = await conn_repo.get_by_store(store.id)
    return {
        "success": True,
        "data": [_connection_response(c) for c in connections],
    }


@router.post(
    "/connections",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Connect social account",
    operation_id="connect_social_account",
)
async def connect_account(
    request: ConnectSocialRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    conn_repo: Annotated[
        SocialConnectionRepository, Depends(_get_social_connection_repo)
    ],
    meta_service: Annotated[MetaSocialService, Depends(_get_meta_service)],
):
    """Initiate or complete an OAuth connection.

    If `oauth_code` is omitted, returns an `auth_url` for the frontend to redirect to.
    If `oauth_code` is provided, completes the connection and returns the connection object.
    """
    try:
        platform = SocialPlatform(request.platform)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid platform '{request.platform}'. Must be 'instagram' or 'facebook'.",
        )

    from src.application.use_cases.social.connect_account import (
        ConnectSocialAccountUseCase,
    )

    use_case = ConnectSocialAccountUseCase(conn_repo, meta_service)

    if not request.oauth_code:
        auth_url = use_case.get_auth_url(platform, request.redirect_uri)
        return {"success": True, "data": {"auth_url": auth_url}}

    connection = await use_case.complete_connection(
        store_id=store.id,
        tenant_id=store.tenant_id,
        platform=platform,
        oauth_code=request.oauth_code,
    )
    return {"success": True, "data": _connection_response(connection).model_dump()}


@router.delete(
    "/connections/{connection_id}",
    response_model=SuccessResponse[dict],
    summary="Disconnect social account",
    operation_id="disconnect_social_account",
)
async def disconnect_account(
    connection_id: Annotated[UUID, Path(description="Connection UUID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    conn_repo: Annotated[
        SocialConnectionRepository, Depends(_get_social_connection_repo)
    ],
):
    """Disconnect a social account and revoke the stored token."""
    from src.application.use_cases.social.disconnect_account import (
        DisconnectSocialAccountUseCase,
    )

    use_case = DisconnectSocialAccountUseCase(conn_repo)
    try:
        await use_case.execute(connection_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return {"success": True, "data": {"message": "Account disconnected"}}


# ---------------------------------------------------------------------------
# US2 — Browse Social Posts
# ---------------------------------------------------------------------------


@router.get(
    "/connections/{connection_id}/posts",
    response_model=SuccessResponse[SocialPostsListResponse],
    summary="Fetch posts from connected account",
    operation_id="fetch_social_posts",
)
async def fetch_posts(
    connection_id: Annotated[UUID, Path(description="Connection UUID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    conn_repo: Annotated[
        SocialConnectionRepository, Depends(_get_social_connection_repo)
    ],
    post_repo: Annotated[SocialPostRepository, Depends(_get_social_post_repo)],
    meta_service: Annotated[MetaSocialService, Depends(_get_meta_service)],
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
):
    """Fetch recent posts from the connected social account."""
    from src.application.use_cases.social.fetch_posts import FetchSocialPostsUseCase

    use_case = FetchSocialPostsUseCase(conn_repo, post_repo, meta_service)

    try:
        posts, next_cursor = await use_case.execute(connection_id, limit, cursor)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    post_responses = [
        SocialPostResponse(
            platform_post_id=p.platform_post_id,
            image_url=p.image_url,
            caption=p.caption,
            likes=p.likes,
            comments=p.comments,
            posted_at=str(p.posted_at) if p.posted_at else None,
            imported=p.is_imported,
            suggested_name=p.suggested_name,
            suggested_name_ar=p.suggested_name_ar,
            suggested_price=p.suggested_price,
        )
        for p in posts
    ]

    return {
        "success": True,
        "data": SocialPostsListResponse(
            posts=post_responses,
            next_cursor=next_cursor,
        ),
    }


# ---------------------------------------------------------------------------
# US3 — Import Posts as Products
# ---------------------------------------------------------------------------


@router.post(
    "/connections/{connection_id}/import",
    response_model=SuccessResponse[ImportPostsResponse],
    summary="Import social posts as draft products",
    operation_id="import_social_posts",
)
async def import_posts(
    connection_id: Annotated[UUID, Path(description="Connection UUID")],
    request: ImportPostsRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    conn_repo: Annotated[
        SocialConnectionRepository, Depends(_get_social_connection_repo)
    ],
    post_repo: Annotated[SocialPostRepository, Depends(_get_social_post_repo)],
    product_repo=Depends(get_product_repository),
):
    """Import one or more social posts as draft NUMU products."""
    from src.application.use_cases.social.import_posts import (
        ImportSocialPostsUseCase,
    )

    use_case = ImportSocialPostsUseCase(conn_repo, post_repo, product_repo)

    try:
        product_ids, errors = await use_case.execute(connection_id, request.post_ids)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return {
        "success": True,
        "data": ImportPostsResponse(
            imported=len(product_ids),
            product_ids=[str(pid) for pid in product_ids],
            errors=errors,
        ),
    }


# ---------------------------------------------------------------------------
# URL-based Import (no OAuth required)
# ---------------------------------------------------------------------------


@router.post(
    "/import-url",
    response_model=SuccessResponse[ImportFromUrlResponse],
    summary="Import products from Instagram/Facebook post URLs",
    operation_id="import_from_social_url",
)
async def import_from_url(
    request: ImportFromUrlRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo=Depends(get_product_repository),
):
    """Import products by pasting Instagram or Facebook post URLs.

    No OAuth connection required — scrapes public post data (image, caption, price)
    from Open Graph meta tags and creates draft products.

    Max 20 URLs per request.
    """
    from src.application.use_cases.social.import_from_url import ImportFromUrlUseCase

    use_case = ImportFromUrlUseCase(product_repo)
    results = await use_case.execute(
        store_id=store.id,
        tenant_id=store.tenant_id,
        urls=request.urls,
    )

    imported_count = sum(1 for r in results if r.product_id)

    return {
        "success": True,
        "data": ImportFromUrlResponse(
            imported=imported_count,
            results=[
                UrlImportResultResponse(
                    url=r.url,
                    product_id=r.product_id,
                    product_name=r.product_name,
                    images_count=r.images_count,
                    error=r.error,
                )
                for r in results
            ],
        ),
    }

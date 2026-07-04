"""Page (merchant content page) routes nested under stores.

URL: /stores/{store_id}/pages
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_page_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.page import (
    CreatePageRequest,
    PageResponse,
    UpdatePageRequest,
)
from src.core.entities.page import Page
from src.core.entities.store import Store
from src.infrastructure.repositories.page_repository import PageRepository

router = APIRouter(prefix="/{store_id}/pages")

_HANDLE_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(handle: str) -> str:
    """Normalize a handle to URL-safe lowercase-with-dashes."""
    s = handle.strip().lower().replace(" ", "-")
    s = _HANDLE_RE.sub("", s)
    return s.strip("-") or "page"


def _page_response(entity: Page) -> PageResponse:
    return PageResponse(
        id=str(entity.id),
        store_id=str(entity.store_id),
        handle=entity.handle,
        title=entity.title or {},
        body=entity.body or {},
        seo=entity.seo or {},
        is_published=entity.is_published,
        template=entity.template or "page",
        template_suffix=entity.template_suffix,
        created_at=str(entity.created_at),
        updated_at=str(entity.updated_at),
    )


async def _revalidate(store: Store, handle: str) -> None:
    """Best-effort: bust the storefront's cached page after a change."""
    if not store.subdomain:
        return
    try:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_page_change,
        )

        await revalidate_on_page_change(
            subdomain=store.subdomain, store_id=str(store.id), handle=handle
        )
    except Exception:
        # Revalidation is non-fatal — the ISR window self-heals.
        pass


@router.get(
    "/",
    response_model=SuccessResponse[list[PageResponse]],
    summary="List pages",
    operation_id="list_pages",
)
async def list_pages(
    store: Annotated[Store, Depends(verify_store_ownership)],
    page_repo: Annotated[PageRepository, Depends(get_page_repository)],
    include_unpublished: bool = Query(True),
):
    """List all content pages for the store."""
    pages = await page_repo.get_by_store(
        store.id, include_unpublished=include_unpublished
    )
    return SuccessResponse(
        data=[_page_response(p) for p in pages],
        message="Pages retrieved successfully",
    )


@router.get(
    "/{handle}",
    response_model=SuccessResponse[PageResponse],
    summary="Get page by handle",
    operation_id="get_page",
)
async def get_page(
    handle: Annotated[str, Path(description="Page handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    page_repo: Annotated[PageRepository, Depends(get_page_repository)],
):
    """Get a single page by handle."""
    page = await page_repo.get_by_handle(store.id, handle)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Page not found"
        )
    return SuccessResponse(
        data=_page_response(page), message="Page retrieved successfully"
    )


@router.post(
    "/",
    response_model=SuccessResponse[PageResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create page",
    operation_id="create_page",
)
async def create_page(
    request: CreatePageRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    page_repo: Annotated[PageRepository, Depends(get_page_repository)],
):
    """Create a new content page for the store."""
    handle = _slugify(request.handle)
    existing = await page_repo.get_by_handle(store.id, handle)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A page with handle '{handle}' already exists",
        )
    page = Page(
        store_id=store.id,
        tenant_id=store.tenant_id,
        handle=handle,
        title=request.title,
        body=request.body,
        seo=request.seo,
        is_published=request.is_published,
        template=request.template or "page",
        template_suffix=request.template_suffix,
    )
    created = await page_repo.create(page)
    await _revalidate(store, handle)
    return SuccessResponse(
        data=_page_response(created), message="Page created successfully"
    )


@router.put(
    "/{handle}",
    response_model=SuccessResponse[PageResponse],
    summary="Update (or create) a page by handle",
    operation_id="update_page",
)
async def update_page(
    handle: Annotated[str, Path(description="Page handle")],
    request: UpdatePageRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    page_repo: Annotated[PageRepository, Depends(get_page_repository)],
):
    """Update a page. If no page with this handle exists, it is created."""
    page = await page_repo.get_by_handle(store.id, handle)
    if not page:
        page = Page(
            store_id=store.id,
            tenant_id=store.tenant_id,
            handle=handle,
            title=request.title or {},
            body=request.body or {},
            seo=request.seo or {},
            is_published=request.is_published
            if request.is_published is not None
            else True,
            template=request.template or "page",
            template_suffix=request.template_suffix,
        )
        created = await page_repo.create(page)
        await _revalidate(store, handle)
        return SuccessResponse(
            data=_page_response(created), message="Page created successfully"
        )

    if request.title is not None:
        page.title = request.title
    if request.body is not None:
        page.body = request.body
    if request.seo is not None:
        page.seo = request.seo
    if request.is_published is not None:
        page.is_published = request.is_published
    if request.template is not None:
        page.template = request.template
    # `template_suffix` is nullable AND clearable: only touch it when the caller
    # actually sent the key so an explicit null clears the variant while an
    # omitted field leaves the current variant untouched.
    if "template_suffix" in request.model_fields_set:
        page.template_suffix = request.template_suffix
    updated = await page_repo.update(page)
    await _revalidate(store, handle)
    return SuccessResponse(
        data=_page_response(updated), message="Page updated successfully"
    )


@router.delete(
    "/{handle}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete page",
    operation_id="delete_page",
)
async def delete_page(
    handle: Annotated[str, Path(description="Page handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    page_repo: Annotated[PageRepository, Depends(get_page_repository)],
):
    """Delete a page by handle."""
    page = await page_repo.get_by_handle(store.id, handle)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Page not found"
        )
    await page_repo.delete(page.id)
    await _revalidate(store, handle)
    return None

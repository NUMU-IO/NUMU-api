"""Menu (store navigation / link list) routes nested under stores.

URL: /stores/{store_id}/menus
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_menu_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.menu import (
    CreateMenuRequest,
    MenuResponse,
    UpdateMenuRequest,
)
from src.core.entities.menu import Menu
from src.core.entities.store import Store
from src.infrastructure.repositories.menu_repository import MenuRepository

router = APIRouter(prefix="/{store_id}/menus")


def _menu_response(entity: Menu) -> MenuResponse:
    return MenuResponse(
        id=str(entity.id),
        store_id=str(entity.store_id),
        handle=entity.handle,
        title=entity.title or {},
        items=entity.items or [],
        is_active=entity.is_active,
        created_at=str(entity.created_at),
        updated_at=str(entity.updated_at),
    )


async def _revalidate(store: Store) -> None:
    """Best-effort: bust the storefront's cached navigation after a change."""
    if not store.subdomain:
        return
    try:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_menu_change,
        )

        await revalidate_on_menu_change(
            subdomain=store.subdomain, store_id=str(store.id)
        )
    except Exception:
        # Revalidation is non-fatal — the 60s ISR window self-heals.
        pass


@router.get(
    "/",
    response_model=SuccessResponse[list[MenuResponse]],
    summary="List menus",
    operation_id="list_menus",
)
async def list_menus(
    store: Annotated[Store, Depends(verify_store_ownership)],
    menu_repo: Annotated[MenuRepository, Depends(get_menu_repository)],
    include_inactive: bool = Query(True),
):
    """List all navigation menus for the store."""
    menus = await menu_repo.get_by_store(store.id, include_inactive=include_inactive)
    return SuccessResponse(
        data=[_menu_response(m) for m in menus],
        message="Menus retrieved successfully",
    )


@router.get(
    "/{handle}",
    response_model=SuccessResponse[MenuResponse],
    summary="Get menu by handle",
    operation_id="get_menu",
)
async def get_menu(
    handle: Annotated[str, Path(description="Menu handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    menu_repo: Annotated[MenuRepository, Depends(get_menu_repository)],
):
    """Get a single menu by handle."""
    menu = await menu_repo.get_by_handle(store.id, handle)
    if not menu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Menu not found"
        )
    return SuccessResponse(
        data=_menu_response(menu), message="Menu retrieved successfully"
    )


@router.post(
    "/",
    response_model=SuccessResponse[MenuResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create menu",
    operation_id="create_menu",
)
async def create_menu(
    request: CreateMenuRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    menu_repo: Annotated[MenuRepository, Depends(get_menu_repository)],
):
    """Create a new navigation menu for the store."""
    existing = await menu_repo.get_by_handle(store.id, request.handle)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A menu with handle '{request.handle}' already exists",
        )
    menu = Menu(
        store_id=store.id,
        tenant_id=store.tenant_id,
        handle=request.handle,
        title=request.title,
        items=[i.model_dump() for i in request.items],
        is_active=request.is_active,
    )
    created = await menu_repo.create(menu)
    await _revalidate(store)
    return SuccessResponse(
        data=_menu_response(created), message="Menu created successfully"
    )


@router.put(
    "/{handle}",
    response_model=SuccessResponse[MenuResponse],
    summary="Update (or create) a menu by handle",
    operation_id="update_menu",
)
async def update_menu(
    handle: Annotated[str, Path(description="Menu handle")],
    request: UpdateMenuRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    menu_repo: Annotated[MenuRepository, Depends(get_menu_repository)],
):
    """Update a menu. If no menu with this handle exists, it is created."""
    menu = await menu_repo.get_by_handle(store.id, handle)
    if not menu:
        menu = Menu(
            store_id=store.id,
            tenant_id=store.tenant_id,
            handle=handle,
            title=request.title or {},
            items=[i.model_dump() for i in (request.items or [])],
            is_active=request.is_active if request.is_active is not None else True,
        )
        created = await menu_repo.create(menu)
        await _revalidate(store)
        return SuccessResponse(
            data=_menu_response(created), message="Menu created successfully"
        )

    if request.title is not None:
        menu.title = request.title
    if request.items is not None:
        menu.items = [i.model_dump() for i in request.items]
    if request.is_active is not None:
        menu.is_active = request.is_active
    updated = await menu_repo.update(menu)
    await _revalidate(store)
    return SuccessResponse(
        data=_menu_response(updated), message="Menu updated successfully"
    )


@router.delete(
    "/{handle}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete menu",
    operation_id="delete_menu",
)
async def delete_menu(
    handle: Annotated[str, Path(description="Menu handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    menu_repo: Annotated[MenuRepository, Depends(get_menu_repository)],
):
    """Delete a menu by handle."""
    menu = await menu_repo.get_by_handle(store.id, handle)
    if not menu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Menu not found"
        )
    await menu_repo.delete(menu.id)
    await _revalidate(store)
    return None

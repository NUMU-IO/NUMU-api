"""Theme update channel routes (Phase 5.1).

URL: /stores/{store_id}/theme-updates

Lets a merchant see when their installed theme has a newer published version,
review what changed (manual vs automatic per Shopify's rules), and apply it.
Applying re-points the store to the latest version through the proven
marketplace install+activate path (snapshot-first via ThemeActivationService)
— a notification never changes a live store on its own, even for an
``automatic`` update (notify-and-confirm).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.dependencies.repositories import (
    get_marketplace_repository,
    get_theme_update_notification_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.marketplace.store_install import _svc as marketplace_service_dep
from src.api.v1.schemas.tenant.theme_update import (
    CheckUpdatesResponse,
    ThemeUpdateNotificationResponse,
)
from src.application.services.marketplace_service import MarketplaceService
from src.application.services.theme_update_service import ThemeUpdateService
from src.core.entities.store import Store
from src.core.entities.theme_update_notification import ThemeUpdateNotification
from src.infrastructure.repositories.marketplace_repository import MarketplaceRepository
from src.infrastructure.repositories.theme_update_notification_repository import (
    ThemeUpdateNotificationRepository,
)

router = APIRouter(prefix="/{store_id}/theme-updates")


def _resp(n: ThemeUpdateNotification) -> ThemeUpdateNotificationResponse:
    return ThemeUpdateNotificationResponse(
        id=str(n.id),
        store_id=str(n.store_id),
        theme_id=str(n.theme_id),
        from_version=n.from_version,
        to_version=n.to_version,
        classification=n.classification,
        changes=n.changes or [],
        release_notes=n.release_notes,
        status=n.status,
        created_at=str(n.created_at),
    )


@router.get(
    "/",
    response_model=SuccessResponse[list[ThemeUpdateNotificationResponse]],
    summary="List theme update notifications",
    operation_id="list_theme_updates",
)
async def list_theme_updates(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        ThemeUpdateNotificationRepository,
        Depends(get_theme_update_notification_repository),
    ],
    status_filter: str | None = Query(
        "pending", alias="status", description="pending|applied|skipped|all"
    ),
):
    """List update notifications for the store (default: pending only)."""
    effective = None if status_filter in (None, "all") else status_filter
    notifs = await repo.get_by_store(store.id, status=effective)
    return SuccessResponse(
        data=[_resp(n) for n in notifs], message="Theme updates retrieved"
    )


@router.post(
    "/check",
    response_model=SuccessResponse[CheckUpdatesResponse],
    summary="Scan for newer versions of installed themes",
    operation_id="check_theme_updates",
)
async def check_theme_updates(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        ThemeUpdateNotificationRepository,
        Depends(get_theme_update_notification_repository),
    ],
    marketplace_repo: Annotated[
        MarketplaceRepository, Depends(get_marketplace_repository)
    ],
):
    """Detect newer published versions for the store's active installs and
    create/refresh pending notifications. Returns all pending notifications."""
    svc = ThemeUpdateService(marketplace_repo, repo)
    created = await svc.check_store(store)
    pending = await repo.get_by_store(store.id, status="pending")
    return SuccessResponse(
        data=CheckUpdatesResponse(notifications=[_resp(n) for n in pending]),
        message=f"{len(created)} update(s) detected",
    )


@router.post(
    "/{notification_id}/skip",
    response_model=SuccessResponse[ThemeUpdateNotificationResponse],
    summary="Skip a theme update",
    operation_id="skip_theme_update",
)
async def skip_theme_update(
    notification_id: str,
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        ThemeUpdateNotificationRepository,
        Depends(get_theme_update_notification_repository),
    ],
):
    """Dismiss a pending update (the merchant chooses not to adopt it)."""
    from uuid import UUID

    notif = await repo.get_by_id(UUID(notification_id))
    if not notif or notif.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    notif.status = "skipped"
    updated = await repo.update(notif)
    return SuccessResponse(data=_resp(updated), message="Update skipped")


@router.post(
    "/{notification_id}/apply",
    response_model=SuccessResponse[ThemeUpdateNotificationResponse],
    summary="Apply a theme update (snapshot-first)",
    operation_id="apply_theme_update",
)
async def apply_theme_update(
    notification_id: str,
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        ThemeUpdateNotificationRepository,
        Depends(get_theme_update_notification_repository),
    ],
    svc: Annotated[MarketplaceService, Depends(marketplace_service_dep)],
    user_id: Annotated[str, Depends(get_current_user_id)],
):
    """Apply the update: install the latest published version and activate it.

    Both steps run through MarketplaceService, which snapshots the current
    active theme into ``store_theme_snapshots`` BEFORE re-pointing
    (ThemeActivationService) — so a merchant can always roll back, and the
    apply is never silent.
    """
    from uuid import UUID

    notif = await repo.get_by_id(UUID(notification_id))
    if not notif or notif.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    if notif.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Update already {notif.status}",
        )

    # Install the latest published version, then activate it (snapshot-first).
    await svc.install_theme(
        store_id=store.id, marketplace_theme_id=notif.theme_id, user_id=user_id
    )
    await svc.activate_theme(
        store_id=store.id, marketplace_theme_id=notif.theme_id, user_id=user_id
    )

    notif.status = "applied"
    updated = await repo.update(notif)
    return SuccessResponse(data=_resp(updated), message="Update applied")

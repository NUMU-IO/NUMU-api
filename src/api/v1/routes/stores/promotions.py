"""Merchant-facing routes for the unified Offers / Promotions system.

URL: /api/v1/stores/{store_id}/promotions

Mirrors the existing `stores/coupons.py` style — same auth stack, same
SuccessResponse envelope, same exception-handler-based error mapping.
"""

from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.promotion_preview import (
    PREVIEW_TOKEN_TTL,
    issue_preview_token,
)
from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_promotion_display_repository,
    get_promotion_event_repository,
    get_promotion_repository,
    get_promotion_target_repository,
    get_promotion_translation_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.application.dto.promotion import (
    CreatePromotionInput,
    PromotionListOutput,
    PromotionMetricsBlock,
    PromotionOutput,
    UpdatePromotionInput,
)
from src.application.dto.promotion_event import PromotionAnalyticsOutput
from src.application.use_cases.promotions.activate_promotion import (
    ActivatePromotionUseCase,
    ArchivePromotionUseCase,
    PausePromotionUseCase,
)
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.application.use_cases.promotions.duplicate_promotion import (
    DuplicatePromotionUseCase,
)
from src.application.use_cases.promotions.get_promotion import GetPromotionUseCase
from src.application.use_cases.promotions.get_promotion_analytics import (
    GetPromotionAnalyticsUseCase,
)
from src.application.use_cases.promotions.list_promotions import (
    ListPromotionsUseCase,
)
from src.application.use_cases.promotions.update_promotion import (
    UpdatePromotionUseCase,
)
from src.core.entities.store import Store
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.infrastructure.events.setup import get_event_bus
from src.infrastructure.repositories import CouponRepository, StoreRepository
from src.infrastructure.repositories.promotion_dismissal_repository import (
    PromotionDismissalRepository,  # noqa: F401 — kept on the DI surface for parity
)
from src.infrastructure.repositories.promotion_event_repository import (
    PromotionEventRepository,
)
from src.infrastructure.repositories.promotion_repository import (
    PromotionDisplayRepository,
    PromotionRepository,
    PromotionTargetRepository,
    PromotionTranslationRepository,
)

router = APIRouter(
    prefix="/{store_id}/promotions",
    # `ff_promotions_v2` gate removed — Promotions is now generally
    # available. Was hiding the entire router (including the preview-token
    # endpoint the merchant hub calls on every promotion edit), so every
    # API request 404'd for tenants without the flag.
)


# --------------------------------------------------------------------------- #
# Bulk reorder                                                                #
# --------------------------------------------------------------------------- #


class ReorderPromotionRow(BaseModel):
    """Single (id, priority) pair for the bulk-reorder endpoint."""

    model_config = ConfigDict(extra="forbid")

    promotion_id: UUID
    priority: int = Field(ge=0, le=10000)


class ReorderPromotionsRequest(BaseModel):
    """Body of `PATCH /stores/{id}/promotions/reorder`.

    Caller sends the full set of (promotion_id, priority) pairs the
    drag UI just produced. The endpoint sets each row's priority in a
    single transaction and bumps `version` on every touched row, so
    concurrent edits from another tab still produce a deterministic
    final order rather than silently losing one side's changes.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ReorderPromotionRow] = Field(min_length=1, max_length=200)


@router.patch(
    "/reorder",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk-update promotion priorities (drag-to-reorder)",
    operation_id="reorder_promotions",
)
async def reorder_promotions(
    body: ReorderPromotionsRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
) -> None:
    """Apply new priority values for a batch of promotions in one go.

    The drag-to-reorder UI sends the full reordered list each time the
    user drops a row; the server doesn't try to compute deltas, just
    overwrites the priority on every id provided. Promotions belonging
    to a different store are silently skipped — defensive against a
    forged payload from a malicious tab.
    """
    # Validate each row's `promotion_id` belongs to `store.id` before
    # we mutate. Cheaper than per-row checks inside the repo because
    # we'd otherwise need a SELECT-then-UPDATE for each row.
    ids = [row.promotion_id for row in body.items]
    existing, _ = await promo_repo.list_for_store(
        store.id, limit=len(ids) + 1, offset=0
    )
    existing_ids = {p.id for p in existing}
    valid_pairs = [
        (row.promotion_id, row.priority)
        for row in body.items
        if row.promotion_id in existing_ids
    ]
    if not valid_pairs:
        return

    # Defer the multi-row UPDATE to the repo so the SQL stays in one
    # place and we get the version bump for free.
    await promo_repo.bulk_set_priority(store.id, valid_pairs)


# --------------------------------------------------------------------------- #
# Preview token                                                               #
# --------------------------------------------------------------------------- #


class PreviewTokenResponse(BaseModel):
    """Short-lived JWT that unlocks draft-state preview on the storefront.

    The merchant hub appends `?_npt=<token>` to the storefront URL when
    opening the builder iframe; the storefront forwards the token as
    `X-Preview-Token` on its server-side promotions fetch.
    """

    token: str = Field(description="Signed preview JWT (5-minute TTL).")
    expires_at: str = Field(description="ISO-8601 expiry of the token.")
    ttl_seconds: int = Field(
        description=(
            "Lifetime of the token in seconds — handy for the merchant "
            "hub to schedule a silent refresh before expiry."
        ),
    )


@router.post(
    "/preview-token",
    response_model=SuccessResponse[PreviewTokenResponse],
    summary="Issue a short-lived preview token for the builder iframe",
    operation_id="issue_promotion_preview_token",
)
async def create_preview_token(
    store: Annotated[Store, Depends(verify_store_ownership)],
) -> SuccessResponse[PreviewTokenResponse]:
    """Mint a 5-minute preview token scoped to this store.

    No request body — the auth + path-scoping (`verify_store_ownership`)
    already establish that the caller is the owner of `store_id`. Token
    is signed with the same JWT key as access / refresh tokens, so it
    inherits the platform's existing rotation story.
    """
    token, expires_at = issue_preview_token(
        store_id=store.id, tenant_id=store.tenant_id
    )
    return SuccessResponse(
        data=PreviewTokenResponse(
            token=token,
            expires_at=expires_at.isoformat(),
            ttl_seconds=int(PREVIEW_TOKEN_TTL.total_seconds()),
        ),
        message="Preview token issued",
    )


# --------------------------------------------------------------------------- #
# List                                                                        #
# --------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=SuccessResponse[PromotionListOutput],
    summary="List promotions for a store",
    operation_id="list_promotions",
)
async def list_promotions(
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    status_filter: Annotated[
        PromotionStatus | None,
        Query(alias="status", description="Filter by status"),
    ] = None,
    surface: Annotated[
        PromotionSurface | None, Query(description="Filter by surface")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuccessResponse[PromotionListOutput]:
    use_case = ListPromotionsUseCase(promotion_repo=promo_repo)
    page = await use_case.execute(
        store_id=store.id,
        status=status_filter,
        surface=surface,
        limit=limit,
        offset=offset,
    )
    return SuccessResponse(data=page)


# --------------------------------------------------------------------------- #
# Get one                                                                     #
# --------------------------------------------------------------------------- #


@router.get(
    "/{promotion_id}",
    response_model=SuccessResponse[PromotionOutput],
    summary="Get a single promotion by id",
    operation_id="get_promotion",
)
async def get_promotion(
    promotion_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
    event_repo: Annotated[
        PromotionEventRepository, Depends(get_promotion_event_repository)
    ],
) -> SuccessResponse[PromotionOutput]:
    use_case = GetPromotionUseCase(
        promotion_repo=promo_repo,
        display_repo=display_repo,
        target_repo=target_repo,
    )
    out = await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
    )
    # Attach lightweight metrics — single query.
    counts = await event_repo.counts_for_promotion(promotion_id)
    out.metrics = PromotionMetricsBlock(
        impressions=counts.impressions,
        clicks=counts.clicks,
        dismissals=counts.dismissals,
        redemptions=counts.redemptions,
        conversions=counts.conversions,
        revenue_cents=counts.revenue_cents,
    )
    return SuccessResponse(data=out)


# --------------------------------------------------------------------------- #
# Create                                                                      #
# --------------------------------------------------------------------------- #


@router.post(
    "",
    response_model=SuccessResponse[PromotionOutput],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new promotion",
    operation_id="create_promotion",
)
async def create_promotion(
    request: CreatePromotionInput,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
    translation_repo: Annotated[
        PromotionTranslationRepository,
        Depends(get_promotion_translation_repository),
    ],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> SuccessResponse[PromotionOutput]:
    use_case = CreatePromotionUseCase(
        promotion_repo=promo_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=get_event_bus(),
    )
    out = await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        actor_user_id=store.owner_id,
        payload=request,
    )
    return SuccessResponse(data=out, message="Promotion created")


# --------------------------------------------------------------------------- #
# Update                                                                      #
# --------------------------------------------------------------------------- #


@router.patch(
    "/{promotion_id}",
    response_model=SuccessResponse[PromotionOutput],
    summary="Update a promotion (optimistic lock via `version`)",
    operation_id="update_promotion",
)
async def update_promotion(
    promotion_id: UUID,
    request: UpdatePromotionInput,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
    translation_repo: Annotated[
        PromotionTranslationRepository,
        Depends(get_promotion_translation_repository),
    ],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
) -> SuccessResponse[PromotionOutput]:
    use_case = UpdatePromotionUseCase(
        promotion_repo=promo_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        event_bus=get_event_bus(),
    )
    out = await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
        actor_user_id=store.owner_id,
        payload=request,
    )
    return SuccessResponse(data=out, message="Promotion updated")


# --------------------------------------------------------------------------- #
# Delete (soft via archive)                                                   #
# --------------------------------------------------------------------------- #


@router.delete(
    "/{promotion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete (archive) a promotion",
    operation_id="delete_promotion",
)
async def delete_promotion(
    promotion_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
) -> None:
    archive = ArchivePromotionUseCase(
        promotion_repo=promo_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        event_bus=get_event_bus(),
    )
    await archive.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
        actor_user_id=store.owner_id,
    )


# --------------------------------------------------------------------------- #
# Duplicate                                                                   #
# --------------------------------------------------------------------------- #


@router.post(
    "/{promotion_id}/duplicate",
    response_model=SuccessResponse[PromotionOutput],
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a promotion as a new draft",
    operation_id="duplicate_promotion",
)
async def duplicate_promotion(
    promotion_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
    translation_repo: Annotated[
        PromotionTranslationRepository,
        Depends(get_promotion_translation_repository),
    ],
) -> SuccessResponse[PromotionOutput]:
    use_case = DuplicatePromotionUseCase(
        promotion_repo=promo_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        event_bus=get_event_bus(),
    )
    out = await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
        actor_user_id=store.owner_id,
        locale=store.default_language or "en",
    )
    return SuccessResponse(data=out, message="Promotion duplicated")


# --------------------------------------------------------------------------- #
# Lifecycle transitions                                                       #
# --------------------------------------------------------------------------- #


def _life_kwargs(
    promo_repo: PromotionRepository,
    display_repo: PromotionDisplayRepository,
    target_repo: PromotionTargetRepository,
) -> dict:
    return {
        "promotion_repo": promo_repo,
        "display_repo": display_repo,
        "target_repo": target_repo,
        "event_bus": get_event_bus(),
    }


@router.post(
    "/{promotion_id}/activate",
    response_model=SuccessResponse[PromotionOutput],
    summary="Move a promotion to ACTIVE",
    operation_id="activate_promotion",
)
async def activate_promotion(
    promotion_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
) -> SuccessResponse[PromotionOutput]:
    activate = ActivatePromotionUseCase(
        **_life_kwargs(promo_repo, display_repo, target_repo)
    )
    out = await activate.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
        actor_user_id=store.owner_id,
    )
    return SuccessResponse(data=out, message="Promotion activated")


@router.post(
    "/{promotion_id}/pause",
    response_model=SuccessResponse[PromotionOutput],
    summary="Move a promotion to PAUSED",
    operation_id="pause_promotion",
)
async def pause_promotion(
    promotion_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
) -> SuccessResponse[PromotionOutput]:
    pause = PausePromotionUseCase(**_life_kwargs(promo_repo, display_repo, target_repo))
    out = await pause.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
        actor_user_id=store.owner_id,
    )
    return SuccessResponse(data=out, message="Promotion paused")


@router.post(
    "/{promotion_id}/archive",
    response_model=SuccessResponse[PromotionOutput],
    summary="Move a promotion to ARCHIVED",
    operation_id="archive_promotion",
)
async def archive_promotion(
    promotion_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
) -> SuccessResponse[PromotionOutput]:
    archive = ArchivePromotionUseCase(
        **_life_kwargs(promo_repo, display_repo, target_repo)
    )
    out = await archive.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
        actor_user_id=store.owner_id,
    )
    return SuccessResponse(data=out, message="Promotion archived")


# --------------------------------------------------------------------------- #
# Analytics                                                                   #
# --------------------------------------------------------------------------- #


@router.get(
    "/{promotion_id}/analytics",
    response_model=SuccessResponse[PromotionAnalyticsOutput],
    summary="Aggregate analytics for one promotion within a date range",
    operation_id="get_promotion_analytics",
)
async def get_promotion_analytics(
    promotion_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    event_repo: Annotated[
        PromotionEventRepository, Depends(get_promotion_event_repository)
    ],
    range_start: Annotated[date | None, Query()] = None,
    range_end: Annotated[date | None, Query()] = None,
) -> SuccessResponse[PromotionAnalyticsOutput]:
    use_case = GetPromotionAnalyticsUseCase(
        promotion_repo=promo_repo, event_repo=event_repo
    )
    out = await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store.id,
        promotion_id=promotion_id,
        range_start=range_start,
        range_end=range_end,
    )
    return SuccessResponse(data=out)


# Reference imports kept above so MyPy / IDEs still see them; suppress the
# unused warning explicitly since `datetime` and `timedelta` are referenced
# through type annotations at import time only.
_ = (datetime, timedelta)

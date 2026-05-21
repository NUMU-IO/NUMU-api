"""Product bundle (Frequently Bought Together) routes for the merchant dashboard.

URL: /stores/{store_id}/bundles

Allows merchants to:
- Create bundle associations between products
- List / get / update / delete bundles
- Reorder bundles (drag & drop)
- Bulk-replace bundles for a product (set operation)

All routes require store ownership verification.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_product_bundle_repository
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.repositories.product_bundle_repository import (
    ProductBundleRepository,
)

router = APIRouter(prefix="/{store_id}/bundles")

# ── Limits ────────────────────────────────────────────────────────────────
MAX_BUNDLES_PER_PRODUCT = 10  # Shopify allows up to 10


# ── Schemas ───────────────────────────────────────────────────────────────


class CreateBundleRequest(BaseModel):
    """Create a single bundle association."""

    primary_product_id: str = Field(
        ..., description="Product whose page shows the widget"
    )
    bundled_product_id: str = Field(..., description="Product to recommend")
    discount_type: str = Field("none", description="none | percentage | fixed")
    discount_value: int = Field(0, ge=0, description="Discount amount (% or cents)")
    position: int = Field(0, ge=0, description="Display order")
    is_active: bool = Field(True)
    section_title_en: str | None = Field(None, max_length=200)
    section_title_ar: str | None = Field(None, max_length=200)


class BulkSetBundlesRequest(BaseModel):
    """Replace all bundles for a primary product (set operation)."""

    primary_product_id: str = Field(..., description="Product whose bundles to replace")
    bundles: list["BundleItemRequest"] = Field(..., max_length=MAX_BUNDLES_PER_PRODUCT)


class BundleItemRequest(BaseModel):
    """A single bundle item in a bulk set operation."""

    bundled_product_id: str
    discount_type: str = "none"
    discount_value: int = 0
    position: int = 0
    is_active: bool = True
    section_title_en: str | None = None
    section_title_ar: str | None = None


class UpdateBundleRequest(BaseModel):
    """Partial update for a bundle."""

    discount_type: str | None = None
    discount_value: int | None = Field(None, ge=0)
    position: int | None = Field(None, ge=0)
    is_active: bool | None = None
    section_title_en: str | None = None
    section_title_ar: str | None = None


class ReorderBundlesRequest(BaseModel):
    """Reorder bundles for a primary product."""

    primary_product_id: str
    ordered_bundle_ids: list[str] = Field(
        ..., description="Bundle IDs in desired display order"
    )


class BundleResponse(BaseModel):
    """Bundle response for the dashboard."""

    id: str
    store_id: str
    primary_product_id: str
    bundled_product_id: str
    discount_type: str
    discount_value: int
    position: int
    is_active: bool
    section_title_en: str | None
    section_title_ar: str | None
    # Bundled product summary (for display without extra API calls)
    bundled_product_name: str | None = None
    bundled_product_price: int | None = None
    bundled_product_image: str | None = None
    bundled_product_in_stock: bool | None = None
    created_at: str
    updated_at: str


# ── Helpers ───────────────────────────────────────────────────────────────


def _bundle_response(bundle) -> BundleResponse:
    """Convert a ProductBundleModel to a BundleResponse."""
    bp = bundle.bundled_product
    return BundleResponse(
        id=str(bundle.id),
        store_id=str(bundle.store_id),
        primary_product_id=str(bundle.primary_product_id),
        bundled_product_id=str(bundle.bundled_product_id),
        discount_type=bundle.discount_type,
        discount_value=bundle.discount_value,
        position=bundle.position,
        is_active=bundle.is_active,
        section_title_en=bundle.section_title_en,
        section_title_ar=bundle.section_title_ar,
        bundled_product_name=bp.name if bp else None,
        bundled_product_price=bp.price_amount if bp else None,
        bundled_product_image=(bp.images[0] if bp and bp.images else None),
        bundled_product_in_stock=(bp.quantity > 0 if bp else None),
        created_at=str(bundle.created_at),
        updated_at=str(bundle.updated_at),
    )


# ── Routes ────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=SuccessResponse[BundleResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create product bundle",
    operation_id="create_product_bundle",
)
async def create_bundle(
    request: CreateBundleRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
):
    """Create a new bundle association for a product."""
    primary_id = UUID(request.primary_product_id)
    bundled_id = UUID(request.bundled_product_id)

    # Prevent self-bundling
    if primary_id == bundled_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A product cannot be bundled with itself",
        )

    # Check duplicate
    if await bundle_repo.exists(store.id, primary_id, bundled_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This bundle pair already exists",
        )

    # Check limit
    count = await bundle_repo.count_by_primary_product(store.id, primary_id)
    if count >= MAX_BUNDLES_PER_PRODUCT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_BUNDLES_PER_PRODUCT} bundles per product",
        )

    bundle = await bundle_repo.create({
        "store_id": store.id,
        "tenant_id": store.tenant_id,
        "primary_product_id": primary_id,
        "bundled_product_id": bundled_id,
        "discount_type": request.discount_type,
        "discount_value": request.discount_value,
        "position": request.position,
        "is_active": request.is_active,
        "section_title_en": request.section_title_en,
        "section_title_ar": request.section_title_ar,
    })

    return SuccessResponse(
        data=_bundle_response(bundle),
        message="Bundle created successfully",
    )


@router.put(
    "/set",
    response_model=SuccessResponse[list[BundleResponse]],
    summary="Set bundles for a product (replace all)",
    operation_id="set_product_bundles",
)
async def set_bundles(
    request: BulkSetBundlesRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
):
    """Replace all bundles for a primary product (atomic set operation).

    This is the preferred endpoint for the dashboard — it sends the
    complete desired state and the backend reconciles.
    """
    primary_id = UUID(request.primary_product_id)

    # Validate no self-bundling and no duplicates
    bundled_ids = set()
    for item in request.bundles:
        bid = UUID(item.bundled_product_id)
        if bid == primary_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A product cannot be bundled with itself",
            )
        if bid in bundled_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate bundled product: {item.bundled_product_id}",
            )
        bundled_ids.add(bid)

    # Delete existing, then create new
    await bundle_repo.delete_all_for_primary(store.id, primary_id)

    items = []
    for idx, item in enumerate(request.bundles):
        items.append({
            "store_id": store.id,
            "tenant_id": store.tenant_id,
            "primary_product_id": primary_id,
            "bundled_product_id": UUID(item.bundled_product_id),
            "discount_type": item.discount_type,
            "discount_value": item.discount_value,
            "position": item.position if item.position else idx,
            "is_active": item.is_active,
            "section_title_en": item.section_title_en,
            "section_title_ar": item.section_title_ar,
        })

    bundles = await bundle_repo.bulk_create(items) if items else []

    return SuccessResponse(
        data=[_bundle_response(b) for b in bundles],
        message="Bundles updated successfully",
    )


@router.get(
    "",
    response_model=SuccessResponse[list[BundleResponse]],
    summary="List bundles for a store or product",
    operation_id="list_product_bundles",
)
async def list_bundles(
    store: Annotated[Store, Depends(verify_store_ownership)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
    primary_product_id: str | None = Query(
        None, description="Filter by primary product ID"
    ),
    active_only: bool = Query(False, description="Only return active bundles"),
):
    """List bundles, optionally filtered by primary product."""
    if primary_product_id:
        bundles = await bundle_repo.list_by_primary_product(
            store_id=store.id,
            primary_product_id=UUID(primary_product_id),
            active_only=active_only,
        )
    else:
        bundles = await bundle_repo.list_by_store(
            store_id=store.id,
            active_only=active_only,
        )

    return SuccessResponse(
        data=[_bundle_response(b) for b in bundles],
        message="Bundles retrieved successfully",
    )


@router.get(
    "/{bundle_id}",
    response_model=SuccessResponse[BundleResponse],
    summary="Get bundle details",
    operation_id="get_product_bundle",
)
async def get_bundle(
    bundle_id: Annotated[UUID, Path(description="Bundle ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
):
    """Get a single bundle by ID."""
    bundle = await bundle_repo.get_by_id(bundle_id, store_id=store.id)
    if not bundle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bundle not found",
        )
    return SuccessResponse(
        data=_bundle_response(bundle),
        message="Bundle retrieved successfully",
    )


@router.patch(
    "/{bundle_id}",
    response_model=SuccessResponse[BundleResponse],
    summary="Update bundle",
    operation_id="update_product_bundle",
)
async def update_bundle(
    bundle_id: Annotated[UUID, Path(description="Bundle ID")],
    request: UpdateBundleRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
):
    """Update a bundle's discount, position, or active state."""
    bundle = await bundle_repo.get_by_id(bundle_id, store_id=store.id)
    if not bundle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bundle not found",
        )

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(bundle, field, value)

    bundle = await bundle_repo.update(bundle)
    return SuccessResponse(
        data=_bundle_response(bundle),
        message="Bundle updated successfully",
    )


@router.delete(
    "/{bundle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete bundle",
    operation_id="delete_product_bundle",
)
async def delete_bundle(
    bundle_id: Annotated[UUID, Path(description="Bundle ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
):
    """Delete a single bundle association."""
    deleted = await bundle_repo.delete(bundle_id=bundle_id, store_id=store.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bundle not found",
        )
    return None


@router.post(
    "/reorder",
    response_model=SuccessResponse,
    summary="Reorder bundles",
    operation_id="reorder_product_bundles",
)
async def reorder_bundles(
    request: ReorderBundlesRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    bundle_repo: Annotated[
        ProductBundleRepository, Depends(get_product_bundle_repository)
    ],
):
    """Reorder bundles for a primary product (drag & drop support)."""
    await bundle_repo.reorder(
        store_id=store.id,
        primary_product_id=UUID(request.primary_product_id),
        ordered_ids=[UUID(bid) for bid in request.ordered_bundle_ids],
    )
    return SuccessResponse(
        data=None,
        message="Bundles reordered successfully",
    )

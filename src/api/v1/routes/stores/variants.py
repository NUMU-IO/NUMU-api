"""Admin product-variant routes — Phase 8.1 follow-up.

URL: /stores/{store_id}/products/{product_id}/variants

The storefront's PDP already reads variants (see
``storefront/public.py::_resolve_variants_for_product``) but the
merchant hub's VariantsEditor was calling a phantom admin CRUD route
that didn't exist. This module exposes the missing endpoints:

    GET    /                  — list variants for a product
    POST   /                  — create a new variant
    PATCH  /{variant_id}      — partial update
    DELETE /{variant_id}      — hard delete (cart/order line items
                                reference variant_id, so callers
                                should treat this as destructive)

Auth: ``verify_store_ownership`` for tenant-scoping, plus a
``_load_product_for_store`` guard so callers can't mutate variants
on a product belonging to another store via path-id confusion.
"""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_product_repository,
    verify_store_ownership,
)
from src.api.dependencies.database import get_db
from src.core.entities.store import Store
from src.core.entities.variant import Variant
from src.core.value_objects.money import Currency, Money
from src.infrastructure.repositories import ProductRepository
from src.infrastructure.repositories.variant_repository import VariantRepository

router = APIRouter(prefix="/{store_id}/products/{product_id}/variants")


# ─── Schemas ──────────────────────────────────────────────────────────
# Wire shape matches what the merchant-hub VariantsEditor sends/reads
# (src/services/variantsApi.ts on the frontend). Decimals are received
# as EGP majors (e.g. `250.00`), persisted via the existing repo
# convention (`int(price.amount)` — same as the storefront write path).


class VariantResponse(BaseModel):
    """Single-variant response. Field names + types intentionally
    mirror the frontend's `Variant` interface so no client-side
    transformation is needed.
    """

    model_config = ConfigDict(from_attributes=False)

    id: str
    product_id: str
    position: int
    option_values: dict[str, str]
    price: str  # Decimal serialized — e.g. "250.00"
    price_currency: str
    compare_at_price: str | None
    cost_price: str | None
    sku: str | None
    barcode: str | None
    inventory_quantity: int
    is_in_stock: bool
    image_url: str | None
    weight_g: float | None
    metadata: dict | None
    created_at: str
    updated_at: str


class CreateVariantRequest(BaseModel):
    """POST body. `price` is required, EGP-majors; everything else
    optional with sensible defaults."""

    option_values: dict[str, str] = Field(default_factory=dict)
    price: Decimal = Field(..., ge=0)
    price_currency: str = Field(default="EGP", max_length=3)
    compare_at_price: Decimal | None = Field(None, ge=0)
    cost_price: Decimal | None = Field(None, ge=0)
    sku: str | None = Field(None, max_length=100)
    barcode: str | None = Field(None, max_length=100)
    inventory_quantity: int = Field(default=0, ge=0)
    image_url: str | None = Field(None, max_length=2048)
    weight_g: float | None = Field(None, ge=0)
    position: int = Field(default=0, ge=0)
    metadata: dict | None = None


class UpdateVariantRequest(BaseModel):
    """PATCH body. Every field optional — only those explicitly set
    are applied. `Field(None)` sentinels distinguish "not sent" from
    "set to null"; for nullable columns the explicit-null clear is
    intentional (e.g. clearing a SKU).
    """

    option_values: dict[str, str] | None = None
    price: Decimal | None = Field(None, ge=0)
    price_currency: str | None = Field(None, max_length=3)
    compare_at_price: Decimal | None = Field(None, ge=0)
    cost_price: Decimal | None = Field(None, ge=0)
    sku: str | None = Field(None, max_length=100)
    barcode: str | None = Field(None, max_length=100)
    inventory_quantity: int | None = Field(None, ge=0)
    image_url: str | None = Field(None, max_length=2048)
    weight_g: float | None = Field(None, ge=0)
    position: int | None = Field(None, ge=0)
    metadata: dict | None = None


# ─── Serializer ───────────────────────────────────────────────────────


def _to_response(v: Variant) -> VariantResponse:
    """Convert a Variant entity to the wire response. Matches the
    storefront PDP's variant summary shape but adds `weight_g`
    (frontend field name) + audit timestamps the admin UI needs."""
    return VariantResponse(
        id=str(v.id),
        product_id=str(v.product_id),
        position=v.position,
        option_values=v.option_values or {},
        price=str(v.price.amount),
        price_currency=(
            v.price.currency.value
            if hasattr(v.price.currency, "value")
            else str(v.price.currency)
        ),
        compare_at_price=(
            str(v.compare_at_price.amount) if v.compare_at_price else None
        ),
        cost_price=str(v.cost_price.amount) if v.cost_price else None,
        sku=v.sku,
        barcode=v.barcode,
        inventory_quantity=v.inventory_quantity,
        is_in_stock=v.is_in_stock,
        image_url=v.image_url,
        weight_g=float(v.weight) if v.weight is not None else None,
        metadata=v.metadata or {},
        created_at=str(v.created_at) if v.created_at else "",
        updated_at=str(v.updated_at) if v.updated_at else "",
    )


def _currency(code: str | None) -> Currency:
    """Coerce a string code into the Currency enum. Falls back to EGP
    (the default for this platform) on unknown codes rather than
    400-ing — most merchants leave currency unset."""
    if not code:
        return Currency.EGP
    try:
        return Currency(code.upper())
    except ValueError:
        return Currency.EGP


# ─── Guard ────────────────────────────────────────────────────────────


async def _load_product_for_store(
    product_id: UUID,
    store: Store,
    product_repo: ProductRepository,
):
    """404 if the product doesn't exist; 404 if it belongs to a
    different store (don't leak existence across tenants — the
    user can't see foreign products in their hub anyway)."""
    product = await product_repo.get_by_id(product_id)
    if product is None or product.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return product


# ─── Routes ───────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[VariantResponse],
    summary="List variants for a product",
    operation_id="list_product_variants",
)
async def list_variants_route(
    product_id: Annotated[UUID, Path(description="Product ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[VariantResponse]:
    """List all variants for the given product, ordered by position."""
    await _load_product_for_store(product_id, store, product_repo)
    repo = VariantRepository(session)
    variants = await repo.list_for_product(product_id)
    return [_to_response(v) for v in variants]


@router.post(
    "",
    response_model=VariantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a variant",
    operation_id="create_product_variant",
)
async def create_variant_route(
    product_id: Annotated[UUID, Path(description="Product ID")],
    request: CreateVariantRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VariantResponse:
    """Create a new variant under the given product. Duplicate
    SKU per store is enforced at the DB layer (uq_variants_store_sku)
    — that raises an IntegrityError we let bubble to FastAPI's 500
    handler today; could be promoted to a 409 in a follow-up."""
    product = await _load_product_for_store(product_id, store, product_repo)
    repo = VariantRepository(session)
    currency = _currency(request.price_currency)
    variant = await repo.create(
        tenant_id=product.tenant_id,
        store_id=store.id,
        product_id=product_id,
        position=request.position,
        option_values=request.option_values,
        price=Money(amount=request.price, currency=currency),
        compare_at_price=(
            Money(amount=request.compare_at_price, currency=currency)
            if request.compare_at_price is not None
            else None
        ),
        cost_price=(
            Money(amount=request.cost_price, currency=currency)
            if request.cost_price is not None
            else None
        ),
        sku=request.sku,
        barcode=request.barcode,
        inventory_quantity=request.inventory_quantity,
        image_url=request.image_url,
        weight=request.weight_g,
        metadata=request.metadata,
    )
    await session.commit()
    return _to_response(variant)


@router.patch(
    "/{variant_id}",
    response_model=VariantResponse,
    summary="Update a variant",
    operation_id="update_product_variant",
)
async def update_variant_route(
    product_id: Annotated[UUID, Path(description="Product ID")],
    variant_id: Annotated[UUID, Path(description="Variant ID")],
    request: UpdateVariantRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VariantResponse:
    """Partial update — only fields explicitly present in the body
    are written. Use null to clear nullable fields (sku, barcode,
    cost_price, etc.)."""
    await _load_product_for_store(product_id, store, product_repo)
    repo = VariantRepository(session)
    existing = await repo.get_by_id(variant_id)
    if existing is None or existing.product_id != product_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Variant not found",
        )

    body = request.model_dump(exclude_unset=True)
    currency = _currency(
        body.get("price_currency")
        or (
            existing.price.currency.value
            if hasattr(existing.price.currency, "value")
            else str(existing.price.currency)
        )
    )

    # Apply only the fields the caller sent. Money objects are
    # immutable, so we rebuild them whenever the amount changes; we
    # keep the same currency unless the caller explicitly switched.
    if "position" in body:
        existing.position = body["position"]
    if "option_values" in body:
        existing.option_values = body["option_values"] or {}
    if "price" in body:
        existing.price = Money(amount=body["price"], currency=currency)
    if "compare_at_price" in body:
        existing.compare_at_price = (
            Money(amount=body["compare_at_price"], currency=currency)
            if body["compare_at_price"] is not None
            else None
        )
    if "cost_price" in body:
        existing.cost_price = (
            Money(amount=body["cost_price"], currency=currency)
            if body["cost_price"] is not None
            else None
        )
    if "sku" in body:
        existing.sku = body["sku"]
    if "barcode" in body:
        existing.barcode = body["barcode"]
    if "inventory_quantity" in body:
        existing.inventory_quantity = body["inventory_quantity"]
    if "image_url" in body:
        existing.image_url = body["image_url"]
    if "weight_g" in body:
        existing.weight = body["weight_g"]
    if "metadata" in body:
        existing.metadata = body["metadata"] or {}

    updated = await repo.update(existing)
    await session.commit()
    return _to_response(updated)


@router.delete(
    "/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a variant",
    operation_id="delete_product_variant",
)
async def delete_variant_route(
    product_id: Annotated[UUID, Path(description="Product ID")],
    variant_id: Annotated[UUID, Path(description="Variant ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Hard delete. Cart/order line items reference variant_id via FK
    — Postgres won't let us delete a variant that's still referenced
    by an active cart line; that surfaces as a 500 today. If we hit
    that in practice we'll need to either soft-delete (add deleted_at)
    or null-out the cart references first."""
    await _load_product_for_store(product_id, store, product_repo)
    repo = VariantRepository(session)
    existing = await repo.get_by_id(variant_id)
    if existing is None or existing.product_id != product_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Variant not found",
        )
    await repo.delete_by_id(variant_id)
    await session.commit()

"""Inventory routes nested under stores.

URL: /stores/{store_id}/inventory
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import (
    get_product_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.repositories import ProductRepository

router = APIRouter(prefix="/{store_id}/inventory")


class InventoryItemResponse(BaseModel):
    """Inventory item response."""

    id: str
    sku: str | None
    name: str
    quantity: int
    low_stock_threshold: int
    is_low_stock: bool
    is_out_of_stock: bool
    price: int  # In cents
    inventory_value: int  # quantity * price in cents
    status: str


class InventoryStatsResponse(BaseModel):
    """Inventory statistics response."""

    total_products: int
    low_stock_count: int
    out_of_stock_count: int
    total_inventory_value: int
    currency: str


class StockAdjustmentRequest(BaseModel):
    """Stock adjustment request."""

    product_id: str
    adjustment: int  # Positive to add, negative to subtract
    reason: str | None = None


class StockAdjustmentResponse(BaseModel):
    """Stock adjustment response."""

    product_id: str
    previous_quantity: int
    new_quantity: int
    adjustment: int


@router.get(
    "/",
    response_model=SuccessResponse[list[InventoryItemResponse]],
    summary="Get inventory list",
    operation_id="get_inventory",
)
async def get_inventory(
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    filter: str = Query("all", description="Filter: all, low_stock, out_of_stock"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    """Get inventory list for the store."""
    # Get products based on filter
    skip = (page - 1) * limit

    if filter == "low_stock":
        products = await product_repo.get_low_stock(store.id, limit=limit)
    elif filter == "out_of_stock":
        products = await product_repo.get_out_of_stock(store.id, limit=limit)
    else:
        products = await product_repo.get_by_store(store.id, skip=skip, limit=limit)

    items = []
    for product in products:
        price_cents = int(product.price.amount * 100) if product.price else 0
        items.append(
            InventoryItemResponse(
                id=str(product.id),
                sku=product.sku,
                name=product.name,
                quantity=product.quantity,
                low_stock_threshold=product.low_stock_threshold,
                is_low_stock=product.is_low_stock,
                is_out_of_stock=product.quantity == 0,
                price=price_cents,
                inventory_value=price_cents * product.quantity,
                status=product.status.value
                if hasattr(product.status, "value")
                else str(product.status),
            )
        )

    return SuccessResponse(
        data=items,
        message="Inventory retrieved successfully",
    )


@router.get(
    "/stats",
    response_model=SuccessResponse[InventoryStatsResponse],
    summary="Get inventory statistics",
    operation_id="get_inventory_stats",
)
async def get_inventory_stats(
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Get inventory statistics for the store."""
    # Get counts
    total_products = await product_repo.count_by_store(store.id)
    low_stock_products = await product_repo.get_low_stock(store.id)
    out_of_stock_products = await product_repo.get_out_of_stock(store.id)

    # Calculate total inventory value
    all_products = await product_repo.get_by_store(store.id, skip=0, limit=10000)
    total_value = 0
    for product in all_products:
        price_cents = int(product.price.amount * 100) if product.price else 0
        total_value += price_cents * product.quantity

    return SuccessResponse(
        data=InventoryStatsResponse(
            total_products=total_products,
            low_stock_count=len(low_stock_products),
            out_of_stock_count=len(out_of_stock_products),
            total_inventory_value=total_value,
            currency=store.default_currency or "EGP",
        ),
        message="Inventory stats retrieved successfully",
    )


@router.post(
    "/adjust",
    response_model=SuccessResponse[StockAdjustmentResponse],
    summary="Adjust stock level",
    operation_id="adjust_stock",
)
async def adjust_stock(
    request: StockAdjustmentRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Adjust stock level for a product."""
    # Get product
    product = await product_repo.get_by_id(UUID(request.product_id))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.store_id != store.id:
        raise HTTPException(
            status_code=403, detail="Product does not belong to this store"
        )

    # Calculate new quantity
    previous_quantity = product.quantity
    new_quantity = max(0, previous_quantity + request.adjustment)

    # Update product
    product.quantity = new_quantity
    await product_repo.update(product)

    return SuccessResponse(
        data=StockAdjustmentResponse(
            product_id=request.product_id,
            previous_quantity=previous_quantity,
            new_quantity=new_quantity,
            adjustment=request.adjustment,
        ),
        message="Stock adjusted successfully",
    )


@router.post(
    "/bulk-adjust",
    response_model=SuccessResponse[list[StockAdjustmentResponse]],
    summary="Bulk adjust stock levels",
    operation_id="bulk_adjust_stock",
)
async def bulk_adjust_stock(
    adjustments: list[StockAdjustmentRequest],
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Bulk adjust stock levels for multiple products."""
    results = []
    for adj in adjustments:
        product = await product_repo.get_by_id(UUID(adj.product_id))
        if not product or product.store_id != store.id:
            continue

        previous_quantity = product.quantity
        new_quantity = max(0, previous_quantity + adj.adjustment)
        product.quantity = new_quantity
        await product_repo.update(product)

        results.append(
            StockAdjustmentResponse(
                product_id=adj.product_id,
                previous_quantity=previous_quantity,
                new_quantity=new_quantity,
                adjustment=adj.adjustment,
            )
        )

    return SuccessResponse(
        data=results,
        message=f"Adjusted {len(results)} products successfully",
    )

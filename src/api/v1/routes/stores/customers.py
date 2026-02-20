"""Customer routes nested under stores (for store owners).

URL: /stores/{store_id}/customers
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from src.api.dependencies.auth import require_store_owner
from src.api.dependencies.repositories import (
    get_customer_repository,
    get_order_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import PaginatedListResponse
from src.api.v1.schemas.public.customer import CustomerResponse
from src.application.use_cases.customers.list_customers import ListCustomersUseCase
from src.infrastructure.repositories import CustomerRepository, OrderRepository

router = APIRouter(prefix="/{store_id}/customers")


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[CustomerResponse]],
    summary="List store customers",
    operation_id="list_store_customers",
)
async def list_store_customers(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    query: str | None = Query(None, description="Search by name or email"),
):
    """List all customers for a store with pagination and search."""
    use_case = ListCustomersUseCase(customer_repository=customer_repo)

    skip = (page - 1) * limit
    result = await use_case.execute(
        store_id=store_id,
        skip=skip,
        limit=limit,
        query=query,
    )

    # Compute live order stats from the orders table (keys are UUID)
    raw_stats = await order_repo.get_customer_order_stats(store_id)
    # Convert keys to str so they match CustomerDTO.id
    order_stats = {str(k): v for k, v in raw_stats.items()}

    customers = [
        CustomerResponse(
            id=customer.id,
            store_id=customer.store_id,
            email=customer.email,
            first_name=customer.first_name,
            last_name=customer.last_name,
            full_name=f"{customer.first_name} {customer.last_name}",
            phone=customer.phone,
            accepts_marketing=customer.accepts_marketing,
            is_verified=customer.is_verified,
            total_orders=order_stats.get(customer.id, (0, 0))[0],
            total_spent=order_stats.get(customer.id, (0, 0))[1],
            default_address_id=customer.default_address_id,
            created_at=str(customer.created_at) if customer.created_at else None,
            updated_at=str(customer.updated_at) if customer.updated_at else None,
        )
        for customer in result.items
    ]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=customers,
            total=result.total,
            page=page,
            page_size=limit,
            total_pages=(result.total + limit - 1) // limit if limit > 0 else 0,
        ),
        message="Customers retrieved successfully",
    )


@router.get(
    "/{customer_id}",
    response_model=SuccessResponse[CustomerResponse],
    summary="Get customer by ID",
    operation_id="get_store_customer",
)
async def get_store_customer(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
):
    """Get a specific customer by ID."""
    from src.core.exceptions import EntityNotFoundError

    customer = await customer_repo.get_by_id(customer_id)

    if not customer or customer.store_id != store_id:
        raise EntityNotFoundError("Customer", str(customer_id))

    # Get live order stats
    raw_stats = await order_repo.get_customer_order_stats(store_id)
    stats = raw_stats.get(customer_id, (0, 0))

    return SuccessResponse(
        data=CustomerResponse(
            id=customer.id,
            store_id=customer.store_id,
            email=customer.email,
            first_name=customer.first_name,
            last_name=customer.last_name,
            full_name=f"{customer.first_name} {customer.last_name}",
            phone=customer.phone,
            accepts_marketing=customer.accepts_marketing,
            is_verified=customer.is_verified,
            total_orders=stats[0],
            total_spent=stats[1],
            default_address_id=customer.default_address_id,
            created_at=str(customer.created_at) if customer.created_at else None,
            updated_at=str(customer.updated_at) if customer.updated_at else None,
        ),
        message="Customer retrieved successfully",
    )

"""Customer routes nested under stores (for store owners).

URL: /stores/{store_id}/customers
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import (
    get_customer_repository,
    get_network_reputation_repository,
    get_order_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import PaginatedListResponse
from src.api.v1.schemas.public.customer import CustomerResponse
from src.application.use_cases.customers.list_customers import ListCustomersUseCase
from src.core.entities.store import Store
from src.infrastructure.repositories import CustomerRepository, OrderRepository
from src.infrastructure.repositories.shopify_repository import (
    NetworkReputationRepository,
)

router = APIRouter(prefix="/{store_id}/customers")


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[CustomerResponse]],
    summary="List store customers",
    operation_id="list_store_customers",
)
async def list_store_customers(
    store: Annotated[Store, Depends(verify_store_ownership)],
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
        store_id=store.id,
        skip=skip,
        limit=limit,
        query=query,
    )

    # Compute live order stats from the orders table (keys are UUID)
    raw_stats = await order_repo.get_customer_order_stats(store.id)
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
    store: Annotated[Store, Depends(verify_store_ownership)],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
):
    """Get a specific customer by ID."""
    from src.core.exceptions import EntityNotFoundError

    customer = await customer_repo.get_by_id(customer_id)

    if not customer or customer.store_id != store.id:
        raise EntityNotFoundError("Customer", str(customer_id))

    # Get live order stats
    raw_stats = await order_repo.get_customer_order_stats(store.id)
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


# ── Trust Stats ────────────────────────────────────────────────────────────


class CustomerTrustStatsResponse(BaseModel):
    """Customer COD trust network statistics."""

    has_data: bool
    network_orders: int
    network_rtos: int
    network_deliveries: int
    network_refunds: int
    contributing_store_count: int
    rto_rate_pct: float  # 0-100
    delivery_rate_pct: float  # 0-100
    risk_score: int  # 0-100
    risk_label: str  # "new_to_network" | "low_risk" | "medium_risk" | "high_risk"
    confidence: str  # "low" | "medium" | "high"
    last_order_at: str | None
    last_rto_at: str | None
    recommendation: str  # "safe", "caution", "risky"


@router.get(
    "/{customer_id}/trust-stats",
    response_model=SuccessResponse[CustomerTrustStatsResponse],
    summary="Get customer COD trust network stats",
    operation_id="get_customer_trust_stats",
)
async def get_customer_trust_stats(
    store: Annotated[Store, Depends(verify_store_ownership)],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    network_repo: Annotated[
        NetworkReputationRepository, Depends(get_network_reputation_repository)
    ],
):
    """Return cross-merchant trust network stats for this customer.

    Stats are keyed on the customer's hashed phone number, so they reflect
    behavior across the entire NUMU merchant network — not just this store.
    """
    from src.application.services.network_reputation_service import (
        extract_phone_hash_from_string,
    )
    from src.core.exceptions import EntityNotFoundError

    customer = await customer_repo.get_by_id(customer_id)
    if not customer or customer.store_id != store.id:
        raise EntityNotFoundError("Customer", str(customer_id))

    empty_response = CustomerTrustStatsResponse(
        has_data=False,
        network_orders=0,
        network_rtos=0,
        network_deliveries=0,
        network_refunds=0,
        contributing_store_count=0,
        rto_rate_pct=0.0,
        delivery_rate_pct=0.0,
        risk_score=55,
        risk_label="new_to_network",
        confidence="low",
        last_order_at=None,
        last_rto_at=None,
        recommendation="safe",
    )

    phone = str(customer.phone) if customer.phone else None
    if not phone:
        return SuccessResponse(
            data=empty_response,
            message="No phone on file — trust stats unavailable",
        )

    phone_hash = extract_phone_hash_from_string(phone)
    if not phone_hash:
        return SuccessResponse(
            data=empty_response,
            message="Phone could not be normalized",
        )

    rep = await network_repo.get_by_phone_hash(phone_hash)
    if not rep:
        return SuccessResponse(
            data=empty_response,
            message="No network reputation data yet",
        )

    # Compute derived metrics
    total = rep.total_network_orders or 0
    rtos = rep.total_network_rtos or 0
    deliveries = rep.total_successful_deliveries or 0
    rto_rate = round((rtos / total) * 100, 1) if total > 0 else 0.0
    delivery_rate = round((deliveries / total) * 100, 1) if total > 0 else 0.0

    # Use cached score if available, otherwise compute fresh
    score = rep.network_risk_score if rep.network_risk_score is not None else 55
    confidence = rep.confidence_level or "low"

    if score >= 70:
        risk_label = "high_risk"
        recommendation = "risky"
    elif score >= 40:
        risk_label = "medium_risk"
        recommendation = "caution"
    else:
        risk_label = "low_risk"
        recommendation = "safe"

    return SuccessResponse(
        data=CustomerTrustStatsResponse(
            has_data=True,
            network_orders=total,
            network_rtos=rtos,
            network_deliveries=deliveries,
            network_refunds=rep.total_refunds or 0,
            contributing_store_count=rep.contributing_store_count or 0,
            rto_rate_pct=rto_rate,
            delivery_rate_pct=delivery_rate,
            risk_score=score,
            risk_label=risk_label,
            confidence=confidence,
            last_order_at=rep.last_order_at.isoformat() if rep.last_order_at else None,
            last_rto_at=rep.last_rto_at.isoformat() if rep.last_rto_at else None,
            recommendation=recommendation,
        ),
        message="Trust stats retrieved",
    )


# ── DSAR: erase a customer's analytics footprint ───────────────────────


class AnalyticsEraseResponse(BaseModel):
    """Result of a DSAR analytics erase request."""

    customer_id: str
    funnel_events_deleted: int
    note: str


@router.delete(
    "/{customer_id}/analytics",
    response_model=SuccessResponse[AnalyticsEraseResponse],
    summary="Erase a customer's analytics footprint (DSAR)",
    operation_id="erase_customer_analytics",
)
async def erase_customer_analytics(
    store: Annotated[Store, Depends(verify_store_ownership)],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Delete every funnel event linked to ``customer_id`` for this store.

    Companion to the existing customer DSAR flows. Only removes events
    where ``customer_id`` is set on the funnel row — anonymous events
    keyed only on ``session_fingerprint`` aren't tied to a person and
    fall under the analytics retention policy instead. Page views are
    already anonymized at write-time (truncated IP, no customer_id).

    Idempotent — re-running on a customer with no events returns 0.
    """
    from sqlalchemy import delete

    from src.core.exceptions import EntityNotFoundError
    from src.infrastructure.database.models.tenant.funnel_event import (
        FunnelEventModel,
    )

    customer = await customer_repo.get_by_id(customer_id)
    if not customer or customer.store_id != store.id:
        raise EntityNotFoundError("Customer", str(customer_id))

    # Reuse the customer_repo session — it's already tenant-scoped via RLS.
    session = customer_repo.session  # type: ignore[attr-defined]
    stmt = delete(FunnelEventModel).where(
        FunnelEventModel.store_id == store.id,
        FunnelEventModel.customer_id == customer_id,
    )
    result = await session.execute(stmt)
    deleted = int(result.rowcount or 0)
    await session.commit()

    return SuccessResponse(
        data=AnalyticsEraseResponse(
            customer_id=str(customer_id),
            funnel_events_deleted=deleted,
            note=(
                "Anonymous events keyed only on session_fingerprint are not "
                "linked to a customer; those are pruned by the retention cron."
            ),
        ),
        message="Customer analytics erased",
    )

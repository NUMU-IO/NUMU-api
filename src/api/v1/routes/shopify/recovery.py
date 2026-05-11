"""Recovery flow read endpoints (backend-021 FR-007).

Three endpoints power spec 009's dashboard surfaces:

- ``GET /{store_id}/recovery/flows`` — paginated list for the recoveries page.
- ``GET /{store_id}/recovery/flows/{flow_id}`` — single flow + step timeline.
- ``GET /{store_id}/recovery/rollup`` — current-month aggregate for the
  headline tile per Principle VI.

The headline tile path stays sub-50ms p99 by reading the indexed
``RecoveryMonthlyRollup`` row directly (no aggregation at read-time).
Write paths (flow creation, payment-success transitions) are owned by
event consumers + Celery tasks; this route file is read-only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies.shopify import (
    get_recovery_flow_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.recovery import (
    RecoveryFlowDetailResponse,
    RecoveryFlowSummaryResponse,
    RecoveryRollupResponse,
    RecoveryStepResponse,
)
from src.core.entities.recovery_flow import RecoveryFlowState
from src.infrastructure.repositories.recovery_flow_repository import (
    RecoveryFlowRepository,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


# Default store-local timezone for "this month" boundary calculation.
# The merchant's actual timezone lives on `Store.timezone` (added in a future
# spec); for v1 we default to Africa/Cairo per the project's MENA focus.
# Spec 002 / spec 010 will introduce a per-store TZ resolver.
DEFAULT_STORE_TIMEZONE = "Africa/Cairo"


def _store_local_first_of_month(store_timezone: str = DEFAULT_STORE_TIMEZONE) -> date:
    """Compute the first day of the *store-local* current calendar month.

    Per constitution v1.2.0 FR-011 the headline tile + billing cycle both
    align on this boundary. Defaulting to Africa/Cairo is a placeholder
    until the per-store TZ resolver lands.
    """
    tz = ZoneInfo(store_timezone)
    now_local = datetime.now(UTC).astimezone(tz)
    return date(now_local.year, now_local.month, 1)


@router.get(
    "/{store_id}/recovery/flows",
    response_model=SuccessResponse[list[RecoveryFlowSummaryResponse]],
    summary="List recovery flows for a store",
    operation_id="shopify_list_recovery_flows",
)
async def list_recovery_flows(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[RecoveryFlowRepository, Depends(get_recovery_flow_repo)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    state: str | None = Query(
        default=None,
        description="Optional state filter (e.g., 'pending_step_1', 'succeeded')",
    ),
):
    parsed_state: RecoveryFlowState | None = None
    if state is not None:
        try:
            parsed_state = RecoveryFlowState(state)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown recovery flow state: {state}",
            ) from exc

    flows = await repo.list_by_store(
        store_id, state=parsed_state, limit=limit, offset=offset
    )
    items = [
        RecoveryFlowSummaryResponse(
            id=f.id,
            store_id=f.store_id,
            shopify_order_id=f.shopify_order_id,
            state=f.state.value,
            current_step_index=f.current_step_index,
            recovered_amount_cents=f.recovered_amount_cents,
            recovered_via_rail=f.recovered_via_rail,
            refunded_at=f.refunded_at,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in flows
    ]
    return SuccessResponse(data=items)


@router.get(
    "/{store_id}/recovery/flows/{flow_id}",
    response_model=SuccessResponse[RecoveryFlowDetailResponse],
    summary="Get a single recovery flow with step timeline",
    operation_id="shopify_get_recovery_flow",
)
async def get_recovery_flow(
    store_id: Annotated[UUID, Path()],
    flow_id: Annotated[UUID, Path()],
    repo: Annotated[RecoveryFlowRepository, Depends(get_recovery_flow_repo)],
):
    flow = await repo.get_by_id(flow_id)
    if flow is None or flow.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recovery flow not found",
        )

    steps = await repo.list_steps_for_flow(flow_id)
    response = RecoveryFlowDetailResponse(
        id=flow.id,
        store_id=flow.store_id,
        shopify_order_id=flow.shopify_order_id,
        state=flow.state.value,
        current_step_index=flow.current_step_index,
        recovered_amount_cents=flow.recovered_amount_cents,
        recovered_via_rail=flow.recovered_via_rail,
        refunded_at=flow.refunded_at,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
        cadence=flow.cadence,
        payment_link_session_id=flow.payment_link_session_id,
        steps=[
            RecoveryStepResponse(
                step_index=s.step_index,
                template_key=s.template_key,
                channel=s.channel,
                scheduled_for=s.scheduled_for,
                sent_at=s.sent_at,
                opened_at=s.opened_at,
                delivered_at=s.delivered_at,
                failed_reason=s.failed_reason,
            )
            for s in steps
        ],
    )
    return SuccessResponse(data=response)


@router.get(
    "/{store_id}/recovery/rollup",
    response_model=SuccessResponse[RecoveryRollupResponse],
    summary="Get current store-local-month recovery rollup",
    operation_id="shopify_get_recovery_rollup",
)
async def get_recovery_rollup(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[RecoveryFlowRepository, Depends(get_recovery_flow_repo)],
    month: str | None = Query(
        default=None,
        description="Override month in YYYY-MM format (defaults to current store-local month)",
        pattern=r"^\d{4}-\d{2}$",
    ),
):
    if month is not None:
        try:
            year_str, month_str = month.split("-")
            month_key = date(int(year_str), int(month_str), 1)
        except (ValueError, IndexError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid month format: {month}; expected YYYY-MM",
            ) from exc
    else:
        month_key = _store_local_first_of_month()

    rollup = await repo.get_rollup(store_id, month_key)
    if rollup is None:
        # Constitution v1.2.0 Principle VI empty-state quality bar: the
        # dashboard renders the empty state when no rollup exists yet.
        # We return zeros explicitly rather than 404 so the UI doesn't
        # have to special-case "no row" vs "row with zeros."
        response = RecoveryRollupResponse(
            store_id=store_id,
            month_key=month_key,
            recovered_cents=0,
            recovered_count=0,
            updated_at=datetime.now(UTC),
        )
        return SuccessResponse(data=response)

    response = RecoveryRollupResponse(
        store_id=rollup.store_id,
        month_key=rollup.month_key.date()
        if isinstance(rollup.month_key, datetime)
        else rollup.month_key,
        recovered_cents=rollup.recovered_cents,
        recovered_count=rollup.recovered_count,
        updated_at=rollup.updated_at,
    )
    return SuccessResponse(data=response)

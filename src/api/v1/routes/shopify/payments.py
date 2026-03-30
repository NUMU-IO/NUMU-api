"""Payment analytics endpoints — channel breakdown and failure reasons."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from src.api.dependencies.shopify import (
    get_payment_transaction_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    FailureReasonSchema,
    PaymentChannelSchema,
    PaymentChannelsResponse,
)
from src.infrastructure.repositories.shopify_repository import (
    PaymentTransactionRepository,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


@router.get(
    "/{store_id}/payments/channels",
    response_model=SuccessResponse[PaymentChannelsResponse],
    summary="Payment channel analytics",
    operation_id="shopify_payment_channels",
)
async def payment_channels(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[
        PaymentTransactionRepository, Depends(get_payment_transaction_repo)
    ],
    days: int = Query(30, ge=1, le=365),
):
    channels_raw = await repo.aggregate_channels(store_id, days=days)
    failures_raw = await repo.aggregate_failures(store_id, days=days)

    # Compute totals for share percentages
    total_revenue = sum((r.get("revenue_cents", 0) or 0) for r in channels_raw)
    total_transactions = sum((r.get("total_attempts", 0) or 0) for r in channels_raw)
    total_successful = sum((r.get("successful_raw", 0) or 0) for r in channels_raw)
    overall_rate = round(
        (total_successful / total_transactions * 100) if total_transactions else 0.0,
        1,
    )

    channels = []
    for row in channels_raw:
        attempts = row.get("total_attempts", 0) or 0
        successful = row.get("successful_raw", 0) or 0
        failed = attempts - successful
        rate = round((successful / attempts * 100) if attempts else 0.0, 1)
        revenue = row.get("revenue_cents", 0) or 0
        share = round((revenue / total_revenue * 100) if total_revenue else 0.0, 1)

        channels.append(
            PaymentChannelSchema(
                channel=row.get("channel", "unknown"),
                gateway=row.get("gateway", "unknown"),
                display_name=row.get("display_name", row.get("channel", "Unknown")),
                total_attempts=attempts,
                successful=successful,
                failed=failed,
                success_rate=rate,
                revenue_cents=revenue,
                revenue_share_pct=share,
            )
        )

    # Failure reasons
    total_failures = sum((r.get("count", 0) or 0) for r in failures_raw)
    failure_reasons = [
        FailureReasonSchema(
            reason=row.get("failure_reason", "Unknown"),
            code=row.get("failure_code"),
            count=row.get("count", 0) or 0,
            pct=round(
                ((row.get("count", 0) or 0) / total_failures * 100)
                if total_failures
                else 0.0,
                1,
            ),
        )
        for row in failures_raw
    ]

    return SuccessResponse(
        data=PaymentChannelsResponse(
            period_days=days,
            total_revenue_cents=total_revenue,
            total_transactions=total_transactions,
            overall_success_rate=overall_rate,
            channels=channels,
            top_failure_reasons=failure_reasons,
        )
    )

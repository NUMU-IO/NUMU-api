"""Payment analytics endpoints — channel breakdown and failure reasons."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal
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


# Channels are equal if their delta is within this band. Below that
# threshold movement is noise; above it the dashboard shows up/down
# arrows. Locked at +-5 percentage points per the spec.
TREND_DELTA_PCT_THRESHOLD = 5.0


def compute_trend(
    *, current_rate: float, prior_rate: float
) -> Literal["up", "down", "stable"]:
    """Bucket a current-vs-prior success-rate delta into a trend label.

    The dashboard renders an arrow per channel; surfacing real trend
    instead of the previous "stable" placeholder is the load-bearing
    bit. Pure function so it's trivially testable.
    """
    delta = current_rate - prior_rate
    if delta > TREND_DELTA_PCT_THRESHOLD:
        return "up"
    if delta < -TREND_DELTA_PCT_THRESHOLD:
        return "down"
    return "stable"


def _success_rate(rows: list[dict], channel: str, gateway: str) -> float:
    """Look up a (channel, gateway) row from an aggregate list and
    return its success rate as a percentage. 0.0 when absent."""
    for row in rows:
        if row.get("channel") == channel and row.get("gateway") == gateway:
            attempts = row.get("total_attempts", 0) or 0
            successful = row.get("successful_raw", 0) or 0
            if attempts:
                return round(successful / attempts * 100, 1)
            return 0.0
    return 0.0


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
    # Current period: most recent ``days``. Prior period: the equal-
    # length window immediately before. Trend = (current - prior).
    period_end = datetime.utcnow()
    period_start = period_end - timedelta(days=days)
    prior_end = period_start
    prior_start = prior_end - timedelta(days=days)

    channels_raw = await repo.aggregate_channels(
        store_id, period_start=period_start, period_end=period_end
    )
    prior_raw = await repo.aggregate_channels(
        store_id, period_start=prior_start, period_end=prior_end
    )
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

        channel_key = row.get("channel", "unknown")
        gateway_key = row.get("gateway", "unknown")
        prior_rate = _success_rate(prior_raw, channel_key, gateway_key)
        trend = compute_trend(current_rate=rate, prior_rate=prior_rate)

        avg_proc_ms_raw = row.get("avg_processing_ms")
        avg_proc_ms = (
            int(round(float(avg_proc_ms_raw))) if avg_proc_ms_raw is not None else None
        )

        channels.append(
            PaymentChannelSchema(
                channel=channel_key,
                gateway=gateway_key,
                display_name=row.get("display_name", channel_key.title()),
                total_attempts=attempts,
                successful=successful,
                failed=failed,
                success_rate=rate,
                revenue_cents=revenue,
                revenue_share_pct=share,
                avg_processing_ms=avg_proc_ms,
                trend=trend,
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

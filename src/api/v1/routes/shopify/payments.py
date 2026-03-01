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

    channels = [
        PaymentChannelSchema(
            channel=row["channel"],
            total=row["total"],
            successful=row["successful"],
            failed=row["failed"],
            success_rate=row["success_rate"],
            revenue_cents=row["revenue_cents"],
        )
        for row in channels_raw
    ]

    failure_reasons = [
        FailureReasonSchema(
            reason=row["reason"],
            count=row["count"],
        )
        for row in failures_raw
    ]

    return SuccessResponse(
        data=PaymentChannelsResponse(
            channels=channels,
            failure_reasons=failure_reasons,
        )
    )

"""Risk narrative endpoint (backend-024)."""

from __future__ import annotations

from typing import Annotated  # noqa: F401  (kept for future Depends type annotations)

from fastapi import APIRouter, Depends

from src.api.dependencies.shopify import verify_internal_key
from src.api.responses import SuccessResponse
from src.api.v1.schemas.risk_narrative import NarrativeRequest, NarrativeResponse
from src.application.services.risk_narrative_service import (
    EntityValuesForTokenization,
    NarrativeFactor,
    generate_narrative,
    tokenize_pii,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


@router.post(
    "/narrative",
    response_model=SuccessResponse[NarrativeResponse],
    summary="Generate risk narrative for a deterministic factor set",
    operation_id="generate_risk_narrative",
)
async def post_risk_narrative(
    request: NarrativeRequest,
) -> SuccessResponse[NarrativeResponse]:
    entities = (
        EntityValuesForTokenization(
            customer_first_name=request.entities.customer_first_name,
            customer_last_name=request.entities.customer_last_name,
            customer_email=request.entities.customer_email,
            customer_phone=request.entities.customer_phone,
            shipping_address1=request.entities.shipping_address1,
            shipping_address2=request.entities.shipping_address2,
            shipping_city=request.entities.shipping_city,
            shipping_country=request.entities.shipping_country,
            order_number=request.entities.order_number,
            shopify_order_id=request.entities.shopify_order_id,
        )
        if request.entities
        else EntityValuesForTokenization()
    )

    factors = [
        NarrativeFactor(
            name=f.name,
            score=f.score,
            weight=f.weight,
            reason_tokenized=tokenize_pii(f.reason, entities),
        )
        for f in request.factors
    ]

    result = await generate_narrative(
        factors=factors,
        purpose=request.purpose,
        language=request.language,
        entities=entities,
    )

    return SuccessResponse(
        data=NarrativeResponse(
            narrative_en=result.narrative_en,
            narrative_ar=result.narrative_ar,
            model_version=result.model_version,
            failure_reason=result.failure_reason,
        )
    )

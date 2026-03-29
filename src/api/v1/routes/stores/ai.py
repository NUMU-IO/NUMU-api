"""AI description generator routes nested under stores.

URL: /stores/{store_id}/ai
"""

import logging
import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.services import get_ai_service
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.ai import (
    GenerateDescriptionRequest,
    GenerateDescriptionResponse,
)
from src.core.entities.store import Store
from src.core.exceptions import ExternalServiceError
from src.infrastructure.external_services.openai import OpenAIService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/ai")

# Simple in-memory rate limiter: store_id -> list of request timestamps
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 10  # requests per store
_RATE_WINDOW = 60  # seconds


def _check_rate_limit(store_id: str) -> None:
    """Enforce 10 req/store/min rate limit."""
    now = time.time()
    timestamps = _rate_limit_store[store_id]
    # Prune old entries
    _rate_limit_store[store_id] = [t for t in timestamps if now - t < _RATE_WINDOW]
    if len(_rate_limit_store[store_id]) >= _RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "AI_RATE_LIMIT",
                "message": "Too many AI generation requests. Please wait before trying again.",
            },
        )
    _rate_limit_store[store_id].append(now)


@router.post(
    "/generate-description",
    response_model=SuccessResponse[GenerateDescriptionResponse],
    summary="Generate bilingual AI product descriptions",
    operation_id="generate_ai_description",
)
async def generate_description(
    request: GenerateDescriptionRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    ai_service: Annotated[OpenAIService, Depends(get_ai_service)],
):
    """Generate bilingual (AR/EN) SEO-optimized product descriptions using AI.

    Uses OpenAI Vision when an image_url is provided, text-only model otherwise.
    Rate limited to 10 requests per store per minute.
    """
    _check_rate_limit(str(store.id))

    try:
        result = await ai_service.generate_bilingual_description(
            product_name=request.product_name,
            product_name_ar=request.product_name_ar,
            category=request.category,
            image_url=request.image_url,
            attributes=request.attributes,
            tone=request.tone,
        )
    except ExternalServiceError as e:
        logger.error("AI service error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_SERVICE_UNAVAILABLE",
                "message": "The AI service is temporarily unavailable. Please try again in a moment.",
            },
        )

    return {
        "success": True,
        "data": GenerateDescriptionResponse(
            short_description_en=result.short_description_en,
            long_description_en=result.long_description_en,
            short_description_ar=result.short_description_ar,
            long_description_ar=result.long_description_ar,
            seo_title_en=result.seo_title_en,
            seo_title_ar=result.seo_title_ar,
            seo_description_en=result.seo_description_en,
            seo_description_ar=result.seo_description_ar,
            tags=result.tags,
        ),
    }

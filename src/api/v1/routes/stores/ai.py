"""AI description generator routes nested under stores.

URL: /stores/{store_id}/ai
"""

import logging
import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from openai import AsyncOpenAI

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.services import get_ai_service
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.ai import (
    GenerateDescriptionRequest,
    GenerateDescriptionResponse,
    GeneratePolicyRequest,
    GeneratePolicyResponse,
)
from src.config import settings
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


_POLICY_PROMPTS: dict[str, dict] = {
    "return": {
        "system": "You are an expert legal copywriter for e-commerce stores.",
        "template": """Generate a professional {lang_name} return/refund policy for an online store called "{store_name}".

Use these details provided by the merchant:
- Return window: {return_window}
- Refund method: {refund_method}
- Conditions for return: {conditions}
- Additional notes: {additional_notes}

Write a clear, professional, and customer-friendly return policy. Use proper headings and formatting. Do NOT use markdown — use plain text with line breaks.""",
    },
    "shipping": {
        "system": "You are an expert legal copywriter for e-commerce stores.",
        "template": """Generate a professional {lang_name} shipping policy for an online store called "{store_name}".

Use these details provided by the merchant:
- Shipping regions: {shipping_regions}
- Estimated delivery time: {delivery_time}
- Shipping cost info: {shipping_cost}
- Additional notes: {additional_notes}

Write a clear, professional, and customer-friendly shipping policy. Use proper headings and formatting. Do NOT use markdown — use plain text with line breaks.""",
    },
    "privacy": {
        "system": "You are an expert legal copywriter specializing in privacy and data protection for e-commerce.",
        "template": """Generate a professional {lang_name} privacy policy for an online store called "{store_name}".

Use these details provided by the merchant:
- Data collected: {data_collected}
- How data is used: {data_usage}
- Third-party sharing: {third_party}
- Additional notes: {additional_notes}

Write a clear, professional, and legally sound privacy policy. Use proper headings and formatting. Do NOT use markdown — use plain text with line breaks.""",
    },
    "terms": {
        "system": "You are an expert legal copywriter for e-commerce stores.",
        "template": """Generate professional {lang_name} terms of service for an online store called "{store_name}".

Use these details provided by the merchant:
- Jurisdiction/country: {jurisdiction}
- Age requirement: {age_requirement}
- Payment terms: {payment_terms}
- Additional notes: {additional_notes}

Write clear, professional, and legally sound terms of service. Use proper headings and formatting. Do NOT use markdown — use plain text with line breaks.""",
    },
}


@router.post(
    "/generate-policy",
    response_model=SuccessResponse[GeneratePolicyResponse],
    summary="Generate a store policy using AI",
    operation_id="generate_ai_policy",
)
async def generate_policy(
    request: GeneratePolicyRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Generate a store policy (return, shipping, privacy, terms) using Qwen via OpenRouter.

    Rate limited to 10 requests per store per minute.
    """
    _check_rate_limit(str(store.id))

    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_SERVICE_UNAVAILABLE",
                "message": "AI service is not configured.",
            },
        )

    policy_config = _POLICY_PROMPTS.get(request.policy_type)
    if not policy_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_POLICY_TYPE",
                "message": f"Invalid policy type: {request.policy_type}. Must be one of: return, shipping, privacy, terms.",
            },
        )

    lang_name = "Arabic" if request.language == "ar" else "English"

    # Build template kwargs from answers, defaulting missing keys
    template_kwargs = {
        "lang_name": lang_name,
        "store_name": request.store_name,
        "additional_notes": request.answers.get("additional_notes", "None"),
    }
    # Fill in all expected placeholders from answers
    for key, value in request.answers.items():
        template_kwargs[key] = value

    try:
        prompt = policy_config["template"].format(**template_kwargs)
    except KeyError:
        # If a placeholder is missing, fill with "Not specified"
        import re

        placeholders = re.findall(r"\{(\w+)\}", policy_config["template"])
        for ph in placeholders:
            if ph not in template_kwargs:
                template_kwargs[ph] = "Not specified"
        prompt = policy_config["template"].format(**template_kwargs)

    try:
        client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )

        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[
                {"role": "system", "content": policy_config["system"]},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.4,
        )

        policy_text = response.choices[0].message.content or ""
    except Exception as e:
        logger.error("AI policy generation error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_SERVICE_UNAVAILABLE",
                "message": "The AI service is temporarily unavailable. Please try again in a moment.",
            },
        )

    return {
        "success": True,
        "data": GeneratePolicyResponse(policy_text=policy_text),
    }

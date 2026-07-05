"""AI description generator routes nested under stores.

URL: /stores/{store_id}/ai
"""

import json
import logging
import re
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
    GeneratePromoContentRequest,
    GeneratePromoContentResponse,
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
    """Generate a store policy (return, shipping, privacy, terms) using Gemini via Google AI Studio.

    Rate limited to 10 requests per store per minute.
    """
    _check_rate_limit(str(store.id))

    if not settings.google_ai_api_key:
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
            api_key=settings.google_ai_api_key,
            base_url=settings.google_ai_base_url,
        )

        response = await client.chat.completions.create(
            model=settings.google_ai_model,
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


# --------------------------------------------------------------------------- #
# Promotion content generation ("Design by NUMU AI")                          #
# --------------------------------------------------------------------------- #

_PROMO_SURFACE_DESC = {
    "announcement_bar": "a store's top announcement bar (a thin one-line strip)",
    "floating_widget": "a small floating corner widget (a pill that expands to a card)",
    "cookie_banner": "a cookie-consent banner",
    "popup": "a popup modal",
}


def _strip_and_sanitize_html(raw: str) -> str:
    """Unwrap markdown fences and strip <script> from AI-generated HTML.

    The storefront renders popup custom_html in a sandboxed iframe (no
    scripts) anyway, but we strip here too so the merchant's live preview
    and the stored value are clean, and cap to the column limit (50000).
    """
    s = (raw or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.IGNORECASE | re.DOTALL)
    return s[:50000]


@router.post(
    "/generate-promo-content",
    response_model=SuccessResponse[GeneratePromoContentResponse],
    summary="Generate promotion content with NUMU AI",
    operation_id="generate_ai_promo_content",
)
async def generate_promo_content(
    request: GeneratePromoContentRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    ai_service: Annotated[OpenAIService, Depends(get_ai_service)],
):
    """Generate promo content with the integrated AI.

    `mode="html"` (popup) returns a script-free, self-contained HTML
    snippet designed for our sandboxed popup iframe. `mode="copy"` returns
    short bilingual (EN + Egyptian Arabic) copy for the banner / floating
    widget / cookie banner. Rate limited to 10 req/store/min.
    """
    _check_rate_limit(str(store.id))

    if ai_service.client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_SERVICE_UNAVAILABLE",
                "message": "AI service is not configured.",
            },
        )

    store_name = request.store_name or store.name or "our store"
    brief = request.brief.strip() or "a general promotional offer"

    try:
        if request.mode == "html" and request.surface == "popup":
            cta = request.cta_url or "https://your-store-link"
            system = "You are an expert front-end designer and e-commerce copywriter."
            user = "\n".join([
                "Design the inner HTML for an e-commerce popup modal. Output "
                "ONLY one self-contained HTML snippet — no <html>/<head>/<body>, "
                "no <script>, no markdown code fences.",
                "Hard requirements (rendered inside a sandboxed iframe ~460px wide):",
                "- Inline CSS only. No <script>, <link>, or external "
                "fonts/images/URLs (they are stripped for security).",
                "- Design for ~460px wide, responsive down to 320px; total "
                "height under ~560px.",
                f'- Include EXACTLY ONE call to action as a link: <a href="{cta}"'
                ' target="_top" style="...">…</a>. target="_top" is REQUIRED so '
                "the click navigates the storefront.",
                '- Write the visible copy in Egyptian Arabic and set dir="rtl" '
                "on the root element.",
                "- Do NOT add a close (X) button — our modal already provides one.",
                f"Brand colors: background {request.primary_color or '#111827'}, "
                f"text {request.text_color or '#ffffff'}.",
                f'Store: "{store_name}". Offer to feature: {brief}.',
                "Return only the HTML.",
            ])
            resp = await ai_service.client.chat.completions.create(
                model=ai_service.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=1600,
                temperature=0.6,
            )
            html = _strip_and_sanitize_html(resp.choices[0].message.content or "")
            return {
                "success": True,
                "data": GeneratePromoContentResponse(mode="html", html=html),
            }

        # copy mode — bilingual short copy for banner / widget / cookie (or
        # popup in template mode).
        surface_desc = _PROMO_SURFACE_DESC.get(request.surface, "a promotion")
        system = (
            "You are a bilingual marketing copywriter for e-commerce. You write "
            "in English and natural Egyptian Arabic. Return ONLY a JSON object."
        )
        user = "\n".join([
            f"Write short marketing copy for {surface_desc} on the store "
            f'"{store_name}".',
            f"Offer / context: {brief}.",
            "Return ONLY a JSON object with EXACTLY these string keys and no "
            "others: headline_en, headline_ar, body_en, body_ar, cta_en, cta_ar.",
            "Rules: headline_* max 60 chars (add one relevant emoji to "
            "headline_en unless it is a cookie banner). body_* max 90 chars. "
            "cta_* max 24 chars. Punchy and on-brand.",
        ])
        resp = await ai_service.client.chat.completions.create(
            model=ai_service.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.7,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {
            "success": True,
            "data": GeneratePromoContentResponse(
                mode="copy",
                headline_en=str(data.get("headline_en") or ""),
                headline_ar=str(data.get("headline_ar") or ""),
                body_en=str(data.get("body_en") or ""),
                body_ar=str(data.get("body_ar") or ""),
                cta_en=str(data.get("cta_en") or ""),
                cta_ar=str(data.get("cta_ar") or ""),
            ),
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — any provider error → graceful 503
        logger.error("AI promo content generation error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AI_SERVICE_UNAVAILABLE",
                "message": "The AI service is temporarily unavailable. Please try again in a moment.",
            },
        )

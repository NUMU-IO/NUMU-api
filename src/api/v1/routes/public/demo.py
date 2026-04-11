"""Public Try-a-Demo provisioning endpoint.

POST /api/v1/public/demo/start — no auth required.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_onboarding_repository,
    get_store_repository,
)
from src.api.dependencies.services import get_password_service, get_token_service
from src.api.responses import SuccessResponse
from src.api.utils.cookies import set_auth_cookies
from src.api.v1.schemas.public.demo import StartDemoRequest, StartDemoResponse
from src.application.use_cases.demo import SeedDemoTenantUseCase, StartDemoUseCase
from src.config import settings
from src.infrastructure.repositories import OnboardingRepository, StoreRepository
from src.infrastructure.tenancy.service import TenantService

logger = logging.getLogger(__name__)
router = APIRouter()

_DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "tempmail.com",
    "10minutemail.com",
    "guerrillamail.com",
    "throwaway.email",
    "yopmail.com",
    "trashmail.com",
    "sharklasers.com",
    "getnada.com",
    "fakeinbox.com",
}


def _is_disposable_email(email: str) -> bool:
    domain = email.lower().split("@")[-1] if "@" in email else ""
    return domain in _DISPOSABLE_DOMAINS


async def _verify_turnstile_token(token: str | None, remote_ip: str | None) -> bool:
    secret = getattr(settings, "turnstile_secret_key", None)
    if not secret:
        return True  # dev mode — no secret configured
    if not token:
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": secret,
                    "response": token,
                    **({"remoteip": remote_ip} if remote_ip else {}),
                },
            )
        return bool(resp.json().get("success"))
    except Exception:
        logger.exception("turnstile_verify_failed")
        return False


@router.post(
    "/demo/start",
    response_model=SuccessResponse[StartDemoResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Start a 7-day Try-a-Demo session",
    operation_id="start_demo",
)
async def start_demo(
    request: StartDemoRequest,
    http_request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    password_service: Annotated[object, Depends(get_password_service)],
    token_service: Annotated[object, Depends(get_token_service)],
):
    """Provision a fresh demo tenant and return an authenticated session."""
    # 1. Disposable email check
    if _is_disposable_email(request.email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please use a real email address.",
        )

    # 2. Turnstile verification
    remote_ip = http_request.client.host if http_request.client else None
    if not await _verify_turnstile_token(request.turnstile_token, remote_ip):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bot verification failed. Please try again.",
        )

    # 3. Wire up use case
    tenant_service = TenantService(db)
    seed_use_case = SeedDemoTenantUseCase(db)
    use_case = StartDemoUseCase(
        db=db,
        tenant_service=tenant_service,
        store_repository=store_repo,
        password_service=password_service,
        token_service=token_service,
        seed_use_case=seed_use_case,
        onboarding_repository=onboarding_repo,
        base_domain=getattr(settings, "storefront_base_domain", "numu.io"),
        dashboard_base_url=getattr(
            settings, "merchant_hub_url", "https://merchant.numueg.app"
        ),
    )

    # 4. Provision
    try:
        result = await use_case.execute(
            captured_email=request.email, language=request.language, niche=request.niche
        )
    except Exception:
        logger.exception("demo_start_failed")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start demo. Please try again in a moment.",
        )

    # 5. Set cross-domain auth cookies
    set_auth_cookies(response, result.access_token, result.refresh_token)

    return SuccessResponse(
        data=StartDemoResponse(
            tenant_id=result.tenant.id,
            store_id=result.store_id,
            subdomain=result.tenant.subdomain,
            expires_at=result.expires_at,
            dashboard_url=result.dashboard_url,
            storefront_url=result.storefront_url,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            message="Your demo store is ready. Have fun exploring.",
        ),
        message="Demo session created",
    )

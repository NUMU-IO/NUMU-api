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
    get_user_repository,
)
from src.api.dependencies.services import (
    get_email_service,
    get_password_service,
    get_token_service,
)
from src.api.responses import SuccessResponse
from src.api.utils.cookies import set_auth_cookies
from src.api.utils.signup_guard import guard_public_signup
from src.api.v1.schemas.public.demo import StartDemoRequest, StartDemoResponse
from src.application.dto.auth import PasswordResetRequestDTO
from src.application.use_cases.auth import ForgotPasswordUseCase
from src.application.use_cases.demo import SeedDemoTenantUseCase, StartDemoUseCase
from src.config import settings
from src.infrastructure.repositories import (
    OnboardingRepository,
    StoreRepository,
    UserRepository,
)
from src.infrastructure.tenancy.service import TenantService

logger = logging.getLogger(__name__)
router = APIRouter()


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
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[object, Depends(get_password_service)],
    token_service: Annotated[object, Depends(get_token_service)],
    email_service: Annotated[object, Depends(get_email_service)],
):
    """Provision a fresh demo tenant and return an authenticated session."""
    # 1. Bot + throwaway-inbox checks, shared with the register endpoint.
    await guard_public_signup(
        email=request.email,
        turnstile_token=request.turnstile_token,
        http_request=http_request,
    )

    # 2. If email already belongs to a real account, send a magic login link
    #    instead of provisioning another demo. Constant-time delay inside the
    #    forgot-password use case prevents enumeration timing attacks.
    existing_user = await user_repo.get_by_email_str(request.email)
    if existing_user is not None:
        forgot_use_case = ForgotPasswordUseCase(
            user_repository=user_repo,
            token_service=token_service,
            email_service=email_service,
        )
        await forgot_use_case.execute(PasswordResetRequestDTO(email=request.email))
        return SuccessResponse(
            data=StartDemoResponse(
                status="magic_link_sent",
                message="We sent a login link to your email.",
            ),
            message="Existing account detected \u2014 magic login link sent",
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
        base_domain=getattr(settings, "storefront_base_domain", "numueg.app"),
        dashboard_base_url=getattr(
            settings, "merchant_hub_url", "https://merchant.numueg.app"
        ),
    )

    # 4. Provision
    try:
        result = await use_case.execute(
            captured_email=request.email,
            captured_name=request.name,
            captured_whatsapp=request.whatsapp,
            language=request.language,
            niche=request.niche,
            attribution=request.attribution,
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

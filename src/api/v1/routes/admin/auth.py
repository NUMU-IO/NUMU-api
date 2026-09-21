"""Admin auth endpoints with an isolated cookie namespace.

URL: /api/v1/admin/auth/*

These endpoints exist so the admin panel can log in without colliding with
the merchant hub session. The regular `/auth/login` sets `access_token`
cookies on `.numueg.app`; an admin who triggered "Log in as merchant"
would see those overwritten by the impersonated merchant's token and be
unceremoniously ejected from the admin panel. The parallel endpoints here
set `admin_access_token` / `admin_refresh_token` instead, which the
`require_admin` dependency resolves before falling back to the shared
cookie name.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_two_factor_repository,
    get_user_repository,
)
from src.api.dependencies.services import (
    get_password_service,
    get_token_service,
    get_totp_service,
)
from src.api.responses import SuccessResponse
from src.api.utils.cookies import (
    clear_admin_auth_cookies,
    set_admin_auth_cookies,
)
from src.api.v1.schemas.public.auth import (
    AuthResponse,
    LoginRequest,
    TokenResponse,
    UserResponse,
)
from src.api.v1.schemas.public.two_factor import (
    Enable2FAResponse,
    TwoFactorStatusResponse,
    Verify2FARequest,
    Verify2FAResponse,
)
from src.application.dto.auth import LoginDTO
from src.application.services.lockout_service import (
    AccountLockoutService,
)
from src.application.use_cases.auth.login import LoginUserUseCase
from src.core.entities.user import UserRole
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.external_services.password_service import PasswordService
from src.infrastructure.external_services.token_service import TokenService
from src.infrastructure.external_services.totp_service import TOTPService
from src.infrastructure.repositories.two_factor_repository import TwoFactorRepository
from src.infrastructure.repositories.user_repository import UserRepository

router = APIRouter()


def _user_response(user) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=str(user.email),
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=user.full_name,
        phone=user.phone,
        role=user.role,
        status=user.status,
        avatar_url=user.avatar_url,
        is_verified=user.is_verified,
        created_at=str(user.created_at),
        updated_at=str(user.updated_at),
        trial_ends_at=str(user.trial_ends_at) if user.trial_ends_at else None,
    )


@router.post(
    "/login",
    response_model=SuccessResponse[AuthResponse],
    summary="Admin login (isolated cookie namespace)",
    operation_id="admin_login",
)
async def admin_login(
    request: LoginRequest,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    _db: Annotated[AsyncSession, Depends(get_db)],
):
    use_case = LoginUserUseCase(
        user_repository=user_repo,
        password_service=password_service,
        token_service=token_service,
        lockout_service=AccountLockoutService(RedisCacheService()),
    )
    result = await use_case.execute(
        LoginDTO(email=request.email, password=request.password)
    )

    # Hard gate: only platform admins can sign in here.
    if result.user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is for platform admins only.",
        )

    set_admin_auth_cookies(
        response,
        result.tokens.access_token,
        result.tokens.refresh_token,
    )

    return SuccessResponse(
        data=AuthResponse(
            user=_user_response(result.user),
            tokens=TokenResponse(
                access_token=result.tokens.access_token,
                refresh_token=result.tokens.refresh_token,
                token_type="bearer",
            ),
        ),
        message="Admin logged in",
    )


@router.get(
    "/me",
    response_model=SuccessResponse[UserResponse],
    summary="Get current admin from admin cookie",
    operation_id="admin_me",
)
async def admin_me(
    admin_id: Annotated[UUID, Depends(require_admin)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
):
    user = await user_repo.get_by_id(admin_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin not found"
        )
    return SuccessResponse(data=_user_response(user))


@router.post(
    "/logout",
    summary="Log out admin (clears admin cookies only)",
    operation_id="admin_logout",
)
async def admin_logout(response: Response, _request: Request):
    clear_admin_auth_cookies(response)
    return SuccessResponse(data=None, message="Logged out")


@router.post(
    "/refresh",
    response_model=SuccessResponse[TokenResponse],
    summary="Refresh admin access token",
    operation_id="admin_refresh",
)
async def admin_refresh(
    request: Request,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    refresh_cookie = request.cookies.get("admin_refresh_token")
    if not refresh_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin refresh token",
        )
    payload = token_service.verify_token(refresh_cookie)
    # The merchant refresh checks this; the admin twin never did, so any
    # valid JWT (an access token, a reset token) could mint an admin pair.
    if payload.token_type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin refresh token",
        )
    user = await user_repo.get_by_id(payload.user_id)
    if not user or user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin not found or no longer has access",
        )
    new_access = token_service.create_access_token(user)
    new_refresh = token_service.create_refresh_token(user)
    set_admin_auth_cookies(response, new_access, new_refresh)
    return SuccessResponse(
        data=TokenResponse(
            access_token=new_access,
            refresh_token=new_refresh,
            token_type="bearer",
        ),
        message="Admin session refreshed",
    )


# ─── Admin 2FA: enrol, and the step-up the gated admin actions require ──────
#
# `require_admin_2fa` (theme, app and partner decisions, capability
# lifecycle, platform settings) accepts an admin only if a TOTP or backup code
# was verified in the last few minutes. The merchant `/auth/2fa/*` routes
# authenticate the MERCHANT session, so an admin working from the admin panel
# had no way to enrol or to step up. With NUMU_FORCE_ADMIN_2FA on, every gated
# action would have been a dead end. These routes are the same use cases
# behind `require_admin` (the admin cookie). A successful verify records the
# use, which is exactly the freshness the step-up checks.


@router.get(
    "/2fa/status",
    response_model=SuccessResponse[TwoFactorStatusResponse],
    summary="The signed-in admin's 2FA status",
    operation_id="admin_2fa_status",
)
async def admin_2fa_status(
    admin_id: Annotated[UUID, Depends(require_admin)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
):
    from src.application.dto.two_factor import TwoFactorStatusDTO

    s = TwoFactorStatusDTO.from_entity(await two_factor_repo.get_by_user_id(admin_id))
    return SuccessResponse(
        data=TwoFactorStatusResponse(
            is_enabled=s.is_enabled,
            method=s.method,
            backup_codes_remaining=s.backup_codes_remaining,
            enabled_at=str(s.enabled_at) if s.enabled_at else None,
            last_used_at=str(s.last_used_at) if s.last_used_at else None,
        )
    )


@router.post(
    "/2fa/enable",
    response_model=SuccessResponse[Enable2FAResponse],
    summary="Start 2FA enrolment for the signed-in admin",
    operation_id="admin_2fa_enable",
)
async def admin_2fa_enable(
    admin_id: Annotated[UUID, Depends(require_admin)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """A TOTP secret, its QR URI and 10 backup codes. Enrolment completes on
    the first successful /2fa/verify."""
    from src.application.use_cases.auth.two_factor import Enable2FAUseCase

    result = await Enable2FAUseCase(
        user_repository=user_repo,
        two_factor_repository=two_factor_repo,
        totp_service=totp_svc,
    ).execute(user_id=admin_id)
    return SuccessResponse(
        data=Enable2FAResponse(
            secret=result.secret,
            provisioning_uri=result.provisioning_uri,
            qr_code_uri=result.qr_code_uri,
            backup_codes=result.backup_codes,
            method=result.method,
        ),
        message="Scan the QR code, then verify a code to finish.",
    )


@router.post(
    "/2fa/verify",
    response_model=SuccessResponse[Verify2FAResponse],
    summary="Finish enrolment, or step up before a gated admin action",
    operation_id="admin_2fa_verify",
)
async def admin_2fa_verify(
    request: Verify2FARequest,
    admin_id: Annotated[UUID, Depends(require_admin)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """A TOTP code or a backup code. Both record the use, so this is the
    step-up: gated actions accept the admin for the next few minutes."""
    from src.application.use_cases.auth.two_factor import Verify2FAUseCase

    current = await two_factor_repo.get_by_user_id(admin_id)
    result = await Verify2FAUseCase(
        two_factor_repository=two_factor_repo, totp_service=totp_svc
    ).execute(
        user_id=admin_id,
        code=request.code,
        is_initial_setup=bool(current and current.is_pending),
    )
    return SuccessResponse(
        data=Verify2FAResponse(
            verified=result.verified,
            method_used=result.method_used,
            backup_codes_remaining=result.backup_codes_remaining,
        )
    )

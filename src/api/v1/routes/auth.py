"""User authentication routes.

These routes handle platform user authentication (not store customers).
Tokens are set via httpOnly cookies — never exposed in JSON response body.
"""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_current_user_id,
    get_password_service,
    get_token_service,
    get_user_repository,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import get_two_factor_repository
from src.api.dependencies.services import (
    get_email_service,
    get_totp_service,
)
from src.api.responses import SuccessResponse
from src.api.utils.cookies import clear_auth_cookies, set_auth_cookies
from src.api.v1.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    CsrfTokenResponse,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    TokenHandoffRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
    VerifyEmailCodeRequest,
    VerifyEmailRequest,
)
from src.api.v1.schemas.public.two_factor import (
    Complete2FALoginRequest,
    Disable2FARequest,
    Enable2FAResponse,
    RegenerateBackupCodesRequest,
    RegenerateBackupCodesResponse,
    TwoFactorStatusResponse,
    Verify2FARequest,
    Verify2FAResponse,
)
from src.application.dto.auth import (
    LoginDTO,
    PasswordResetDTO,
    PasswordResetRequestDTO,
    RefreshTokenDTO,
    RegisterDTO,
)
from src.application.services.lockout_service import AccountLockoutService
from src.application.services.refresh_token_blacklist_service import (
    RefreshTokenBlacklistService,
)
from src.application.services.token_revocation_service import TokenRevocationService
from src.application.use_cases.auth import (
    ChangePasswordDTO,
    ChangePasswordUseCase,
    ForgotPasswordUseCase,
    LoginUserUseCase,
    RefreshTokenUseCase,
    RegisterUserUseCase,
    ResetPasswordUseCase,
    UpdateProfileDTO,
    UpdateProfileUseCase,
    VerifyEmailUseCase,
)
from src.application.use_cases.auth.two_factor import (
    CompleteTwoFactorLoginUseCase,
    Disable2FAUseCase,
    Enable2FAUseCase,
    RegenerateBackupCodesUseCase,
    Verify2FAUseCase,
)
from src.config import settings
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.external_services import (
    PasswordService,
    ResendEmailService,
    TokenService,
)
from src.infrastructure.external_services.totp_service import TOTPService
from src.infrastructure.repositories import TwoFactorRepository, UserRepository

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper to build UserResponse from auth result
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CSRF Token
# ---------------------------------------------------------------------------


@router.get(
    "/csrf-token",
    response_model=SuccessResponse[CsrfTokenResponse],
    summary="Get CSRF token",
    operation_id="get_csrf_token",
)
async def get_csrf_token(response: Response):
    """Generate a CSRF token.

    Sets a non-httpOnly `csrf_token` cookie so JavaScript can read it,
    and also returns the value in the response body.
    The client must send this token in the `X-CSRF-Token` header
    on every state-changing request.
    """
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key="csrf_token",
        value=token,
        httponly=False,  # JS must be able to read this
        secure=settings.SECURE_COOKIES,
        samesite=settings.SAMESITE_COOKIES,
        domain=settings.COOKIE_DOMAIN,
        path="/",
        max_age=86400,  # 24 hours
    )
    return SuccessResponse(
        data=CsrfTokenResponse(csrf_token=token),
        message="CSRF token generated",
    )


# ---------------------------------------------------------------------------
# Token Handoff  (landing-page → dashboard cross-origin redirect)
# ---------------------------------------------------------------------------


@router.post(
    "/token-handoff",
    response_model=SuccessResponse[AuthResponse],
    summary="Exchange tokens from URL params into httpOnly cookies",
    operation_id="token_handoff",
)
async def token_handoff(
    request: TokenHandoffRequest,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Accept the access_token & refresh_token passed as URL query params
    after a cross-origin redirect from the landing page, validate them,
    then set fresh httpOnly cookies so the dashboard can operate normally.
    """
    payload = token_service.verify_token(request.access_token)
    user = await user_repo.get_by_id(payload.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Issue a fresh pair of tokens so the redirected session is independent
    new_access = token_service.create_access_token(user)
    new_refresh = token_service.create_refresh_token(user)
    set_auth_cookies(response, new_access, new_refresh)

    return SuccessResponse(
        data=AuthResponse(
            user=_user_response(user),
            tokens=TokenResponse(
                access_token=new_access,
                refresh_token=new_refresh,
                token_type="bearer",
            ),
        ),
        message="Session established",
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=SuccessResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register new user",
    operation_id="register",
)
async def register(
    request: RegisterRequest,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    email_service: Annotated[ResendEmailService, Depends(get_email_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Register a new platform user account.

    Tokens are set as httpOnly cookies — not included in the JSON body.
    A verification email is sent after registration.
    """
    # Signup gate — the admin can turn new-merchant registration off from
    # the platform settings page. Check this BEFORE we do any work so we
    # fail fast with a clear message.
    from src.api.v1.routes.admin.platform_settings import get_platform_settings

    platform = await get_platform_settings(db)
    if not platform.get("enable_new_merchant_signups", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="New merchant signups are currently disabled. Please try again later.",
        )

    use_case = RegisterUserUseCase(
        user_repository=user_repo,
        password_service=password_service,
        token_service=token_service,
        email_service=email_service,
    )

    dto = RegisterDTO(
        email=request.email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
    )
    result = await use_case.execute(dto)

    # Set tokens as httpOnly cookies
    set_auth_cookies(
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
        message="User registered successfully",
    )


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------


@router.post(
    "/google",
    response_model=SuccessResponse[AuthResponse],
    summary="Sign in with Google",
    operation_id="google_oauth",
)
async def google_oauth(
    request: Request,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Authenticate or register a user via Google ID token.

    Expects JSON body: { "id_token": "..." }
    If the user doesn't exist, creates a new auto-verified account.
    If the user exists (by Google ID or email), logs them in.
    """
    from src.application.use_cases.auth.google_oauth import GoogleOAuthUseCase

    body = await request.json()
    id_token_str = body.get("id_token")
    if not id_token_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="id_token is required",
        )

    use_case = GoogleOAuthUseCase(
        user_repository=user_repo,
        token_service=token_service,
    )

    try:
        result = await use_case.execute(id_token_str)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from e

    # Set auth cookies
    set_auth_cookies(
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
        message="Google authentication successful",
    )


# ---------------------------------------------------------------------------
# Verify Email
# ---------------------------------------------------------------------------


@router.post(
    "/verify-email",
    response_model=SuccessResponse[MessageResponse],
    summary="Verify email address",
    operation_id="verify_email",
)
async def verify_email(
    request: VerifyEmailRequest,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Verify a user's email address using the token from the verification email."""
    use_case = VerifyEmailUseCase(
        user_repository=user_repo,
        token_service=token_service,
    )
    await use_case.execute(request.token)

    # Dispatch welcome email now that the address is verified
    try:
        payload = token_service.verify_token(request.token)
        user = await user_repo.get_by_id(payload.user_id)
        if user:
            from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
                send_welcome_email_task,
            )

            send_welcome_email_task.delay(
                email=str(user.email),
                merchant_name=user.first_name or "",
            )
    except Exception:
        pass  # Non-critical; don't block verification response

    return SuccessResponse(
        data=MessageResponse(message="Email verified successfully"),
        message="Email verified successfully",
    )


# ---------------------------------------------------------------------------
# Verify Email by Code
# ---------------------------------------------------------------------------


@router.post(
    "/verify-email-code",
    response_model=SuccessResponse[MessageResponse],
    summary="Verify email with 6-digit code",
    operation_id="verify_email_code",
)
async def verify_email_code(
    request: VerifyEmailCodeRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
):
    """Verify a user's email using the 6-digit code sent to their email."""
    from src.infrastructure.cache.redis_cache import RedisCacheService

    cache = RedisCacheService()
    cache_key = f"email_verify_code:{user_id}"
    stored_code = await cache.get(cache_key)

    if not stored_code or stored_code != request.code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    # Code matches — verify the user
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.is_verified:
        user.verify_email()
        await user_repo.update(user)

        # Dispatch welcome email now that the address is verified
        try:
            from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
                send_welcome_email_task,
            )

            send_welcome_email_task.delay(
                email=str(user.email),
                merchant_name=user.first_name or "",
            )
        except Exception:
            pass  # Non-critical; don't block verification response

    # Clean up the used code
    await cache.delete(cache_key)

    return SuccessResponse(
        data=MessageResponse(message="Email verified successfully"),
        message="Email verified successfully",
    )


# ---------------------------------------------------------------------------
# Resend Verification Email
# ---------------------------------------------------------------------------


@router.post(
    "/resend-verification",
    response_model=SuccessResponse[MessageResponse],
    summary="Resend verification email",
    operation_id="resend_verification",
)
async def resend_verification(
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    email_service: Annotated[ResendEmailService, Depends(get_email_service)],
):
    """Resend the verification email with a new code and link."""
    import random

    from src.infrastructure.cache.redis_cache import RedisCacheService

    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_verified:
        return SuccessResponse(
            data=MessageResponse(message="Email is already verified"),
            message="Email is already verified",
        )

    # Generate new code and store in Redis
    code = f"{random.randint(0, 999999):06d}"
    cache = RedisCacheService()
    await cache.set(
        f"email_verify_code:{user_id}",
        code,
        expire=86400,  # 24 hours
    )

    # Generate new verification token (link)
    verification_token = token_service.create_email_verification_token(user)

    # Send the email
    await email_service.send_verification_email(
        email=str(user.email),
        token=verification_token,
        code=code,
    )

    return SuccessResponse(
        data=MessageResponse(message="Verification email sent"),
        message="Verification email sent",
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=SuccessResponse[AuthResponse],
    summary="Login user",
    operation_id="login",
)
async def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Authenticate user and set tokens as httpOnly cookies.

    If the user has 2FA enabled, returns a challenge token instead of full auth tokens.
    The client must exchange the challenge token at /auth/2fa/complete-login with a valid code.
    """
    use_case = LoginUserUseCase(
        user_repository=user_repo,
        password_service=password_service,
        token_service=token_service,
        lockout_service=AccountLockoutService(RedisCacheService()),
    )

    dto = LoginDTO(
        email=request.email,
        password=request.password,
    )
    result = await use_case.execute(dto)

    # Check if user has 2FA enabled — if so, return a challenge token instead
    from uuid import UUID as _UUID

    user_uuid = (
        _UUID(str(result.user.id))
        if isinstance(result.user.id, str)
        else result.user.id
    )
    if await two_factor_repo.user_has_2fa_enabled(user_uuid):
        challenge_token = token_service.create_challenge_token(user_uuid)
        return SuccessResponse(
            data=AuthResponse(
                requires_2fa=True,
                challenge_token=challenge_token,
            ),
            message="2FA verification required",
        )

    # Set tokens as httpOnly cookies
    set_auth_cookies(
        response,
        result.tokens.access_token,
        result.tokens.refresh_token,
    )

    # Record login session
    try:
        from src.application.services.session_service import record_session

        ip = http_request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            http_request.client.host if http_request.client else None
        )
        ua = http_request.headers.get("user-agent", "")
        await record_session(db, user_uuid, user_agent=ua, ip_address=ip)
        await db.commit()
    except Exception:
        pass  # Non-critical — don't block login

    return SuccessResponse(
        data=AuthResponse(
            user=_user_response(result.user),
            tokens=TokenResponse(
                access_token=result.tokens.access_token,
                refresh_token=result.tokens.refresh_token,
                token_type="bearer",
            ),
        ),
        message="Login successful",
    )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=SuccessResponse[MessageResponse],
    summary="Refresh access token",
    operation_id="refresh_token",
)
async def refresh_token(
    request: Request,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Refresh access token using refresh_token cookie.

    No request body needed — the refresh token is read from the httpOnly cookie.
    New tokens are set as httpOnly cookies on the response.
    """
    refresh_tok = request.cookies.get("refresh_token")
    if not refresh_tok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found",
        )

    use_case = RefreshTokenUseCase(
        user_repository=user_repo,
        token_service=token_service,
        blacklist_service=RefreshTokenBlacklistService(RedisCacheService()),
    )

    dto = RefreshTokenDTO(refresh_token=refresh_tok)
    result = await use_case.execute(dto)

    # Set new tokens as httpOnly cookies
    set_auth_cookies(response, result.access_token, result.refresh_token)

    return SuccessResponse(
        data=MessageResponse(message="Token refreshed"),
        message="Token refreshed successfully",
    )


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    response_model=SuccessResponse[MessageResponse],
    summary="Logout user",
    operation_id="logout",
)
async def logout(response: Response):
    """Clear auth cookies to log the user out."""
    clear_auth_cookies(response)
    response.delete_cookie(key="csrf_token", path="/", domain=settings.COOKIE_DOMAIN)
    return SuccessResponse(
        data=MessageResponse(message="Logged out successfully"),
        message="Logged out successfully",
    )


# ---------------------------------------------------------------------------
# Forgot / Reset Password (unchanged logic, no tokens in body)
# ---------------------------------------------------------------------------


@router.post(
    "/forgot-password",
    response_model=SuccessResponse[MessageResponse],
    summary="Request password reset",
    description="Send password reset email to user.",
    operation_id="forgot_password",
)
async def forgot_password(
    request: PasswordResetRequest,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    email_service: Annotated[ResendEmailService, Depends(get_email_service)],
):
    """Initiate password reset process."""
    use_case = ForgotPasswordUseCase(
        user_repository=user_repo,
        token_service=token_service,
        email_service=email_service,
    )

    dto = PasswordResetRequestDTO(email=request.email)
    await use_case.execute(dto)

    return SuccessResponse(
        data=MessageResponse(message="If the email exists, a reset link has been sent"),
        message="Password reset request received",
    )


@router.post(
    "/reset-password",
    response_model=SuccessResponse[MessageResponse],
    summary="Reset password",
    description="Reset password using token from email.",
    operation_id="reset_password",
)
async def reset_password(
    request: PasswordResetConfirm,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
):
    """Reset user password using token."""
    use_case = ResetPasswordUseCase(
        user_repository=user_repo,
        token_service=token_service,
        password_service=password_service,
    )

    dto = PasswordResetDTO(
        token=request.token,
        new_password=request.new_password,
    )
    await use_case.execute(dto)

    return SuccessResponse(
        data=MessageResponse(message="Password has been reset successfully"),
        message="Password reset successful",
    )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=SuccessResponse[UserResponse],
    summary="Get current user",
    operation_id="get_current_user",
)
async def get_current_user(
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get current authenticated user's profile + tenant lifecycle state."""
    user = await user_repo.get_by_id(user_id)

    if not user:
        raise EntityNotFoundError("User", str(user_id))

    # Resolve tenant owned by this user (if any)
    tenant_info = None
    try:
        from sqlalchemy import select

        from src.api.v1.schemas.public.auth import TenantInfoResponse
        from src.infrastructure.database.models.public.tenant import TenantModel

        tenant_q = select(TenantModel).where(TenantModel.owner_id == user.id)
        tenant = (await db.execute(tenant_q)).scalar_one_or_none()
        if tenant:
            tenant_info = TenantInfoResponse(
                id=str(tenant.id),
                name=tenant.name,
                subdomain=tenant.subdomain,
                plan=tenant.plan,
                lifecycle_state=tenant.lifecycle_state,
                is_demo=tenant.is_demo,
                is_on_trial=tenant.is_on_trial,
                is_read_only=tenant.is_read_only,
                is_writable=tenant.is_writable,
                expires_at=tenant.expires_at.isoformat() if tenant.expires_at else None,
                days_remaining=tenant.days_remaining,
                demo_email=tenant.demo_email,
            )
    except Exception:
        pass  # Non-critical — old tenants without lifecycle columns still work

    return SuccessResponse(
        data=UserResponse(
            id=str(user.id),
            email=user.email.value,
            first_name=user.first_name,
            last_name=user.last_name,
            full_name=f"{user.first_name} {user.last_name}",
            phone=user.phone.value if user.phone else None,
            role=user.role.value,
            status=user.status.value,
            avatar_url=user.avatar_url,
            is_verified=user.is_verified,
            created_at=str(user.created_at),
            updated_at=str(user.updated_at),
            trial_ends_at=str(user.trial_ends_at) if user.trial_ends_at else None,
            tenant=tenant_info,
        ),
        message="User retrieved successfully",
    )


@router.patch(
    "/me",
    response_model=SuccessResponse[UserResponse],
    summary="Update profile",
    operation_id="update_profile",
)
async def update_profile(
    request: UpdateProfileRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
):
    """Update current user's profile."""
    import logging
    from uuid import UUID

    logger = logging.getLogger(__name__)

    try:
        use_case = UpdateProfileUseCase(user_repository=user_repo)

        dto = UpdateProfileDTO(
            first_name=request.first_name,
            last_name=request.last_name,
            phone=request.phone,
            avatar_url=request.avatar_url,
        )

        result = await use_case.execute(
            user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
            dto=dto,
        )

        return SuccessResponse(
            data=UserResponse(
                id=str(result.id),
                email=result.email,
                first_name=result.first_name,
                last_name=result.last_name,
                full_name=result.full_name,
                phone=result.phone,
                role=result.role,
                status=result.status,
                avatar_url=result.avatar_url,
                is_verified=result.is_verified,
                created_at=str(result.created_at),
                updated_at=str(result.updated_at),
                trial_ends_at=str(getattr(result, "trial_ends_at", None))
                if getattr(result, "trial_ends_at", None)
                else None,
            ),
            message="Profile updated successfully",
        )
    except Exception as e:
        logger.exception(f"PATCH /auth/me failed: {type(e).__name__}: {e}")
        raise


@router.patch(
    "/me/password",
    response_model=SuccessResponse[MessageResponse],
    summary="Change password",
    operation_id="change_password",
)
async def change_password(
    request: ChangePasswordRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
):
    """Change current user's password."""
    from uuid import UUID

    use_case = ChangePasswordUseCase(
        user_repository=user_repo,
        password_service=password_service,
        revocation_service=TokenRevocationService(RedisCacheService()),
    )

    dto = ChangePasswordDTO(
        current_password=request.current_password,
        new_password=request.new_password,
    )

    await use_case.execute(
        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
        dto=dto,
    )

    return SuccessResponse(
        data=MessageResponse(message="Password changed successfully"),
        message="Password changed successfully",
    )


# =============================================================================
# Two-Factor Authentication (2FA) Routes — unchanged
# =============================================================================


@router.post(
    "/2fa/enable",
    response_model=SuccessResponse[Enable2FAResponse],
    summary="Enable 2FA",
    description="Generate TOTP secret, QR code URI, and 10 backup codes to enable 2FA.",
    operation_id="enable_2fa",
)
async def enable_2fa(
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """Enable Two-Factor Authentication for the current user."""
    from uuid import UUID

    use_case = Enable2FAUseCase(
        user_repository=user_repo,
        two_factor_repository=two_factor_repo,
        totp_service=totp_svc,
    )

    result = await use_case.execute(
        user_id=UUID(user_id) if isinstance(user_id, str) else user_id
    )

    return SuccessResponse(
        data=Enable2FAResponse(
            secret=result.secret,
            provisioning_uri=result.provisioning_uri,
            qr_code_uri=result.qr_code_uri,
            backup_codes=result.backup_codes,
            method=result.method,
        ),
        message="2FA setup initiated. Verify with a TOTP code to complete.",
    )


@router.post(
    "/2fa/verify",
    response_model=SuccessResponse[Verify2FAResponse],
    summary="Verify 2FA code",
    description="Verify a TOTP code or backup code. Completes 2FA setup if pending.",
    operation_id="verify_2fa",
)
async def verify_2fa(
    request: Verify2FARequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """Verify a 2FA code (TOTP or backup code)."""
    from uuid import UUID

    two_factor = await two_factor_repo.get_by_user_id(
        UUID(user_id) if isinstance(user_id, str) else user_id
    )
    is_initial_setup = two_factor and two_factor.is_pending

    use_case = Verify2FAUseCase(
        two_factor_repository=two_factor_repo,
        totp_service=totp_svc,
    )

    result = await use_case.execute(
        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
        code=request.code,
        is_initial_setup=is_initial_setup,
    )

    message = "2FA verified successfully"
    if is_initial_setup:
        message = "2FA enabled successfully! Your account is now protected."

    return SuccessResponse(
        data=Verify2FAResponse(
            verified=result.verified,
            method_used=result.method_used,
            backup_codes_remaining=result.backup_codes_remaining,
        ),
        message=message,
    )


@router.delete(
    "/2fa/disable",
    response_model=SuccessResponse[TwoFactorStatusResponse],
    summary="Disable 2FA",
    description="Disable Two-Factor Authentication. Requires password confirmation.",
    operation_id="disable_2fa",
)
async def disable_2fa(
    request: Disable2FARequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    password_svc: Annotated[PasswordService, Depends(get_password_service)],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """Disable Two-Factor Authentication for the current user."""
    from uuid import UUID

    use_case = Disable2FAUseCase(
        user_repository=user_repo,
        two_factor_repository=two_factor_repo,
        password_service=password_svc,
        totp_service=totp_svc,
    )

    result = await use_case.execute(
        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
        password=request.password,
        totp_code=request.code,
    )

    return SuccessResponse(
        data=TwoFactorStatusResponse(
            is_enabled=result.is_enabled,
            method=result.method,
            backup_codes_remaining=result.backup_codes_remaining,
            enabled_at=str(result.enabled_at) if result.enabled_at else None,
            last_used_at=str(result.last_used_at) if result.last_used_at else None,
        ),
        message="2FA disabled successfully",
    )


@router.get(
    "/2fa/status",
    response_model=SuccessResponse[TwoFactorStatusResponse],
    summary="Get 2FA status",
    description="Get the current 2FA status for the authenticated user.",
    operation_id="get_2fa_status",
)
async def get_2fa_status(
    user_id: Annotated[str, Depends(get_current_user_id)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
):
    """Get the current 2FA status for the authenticated user."""
    from uuid import UUID

    from src.application.dto.two_factor import TwoFactorStatusDTO

    two_factor = await two_factor_repo.get_by_user_id(
        UUID(user_id) if isinstance(user_id, str) else user_id
    )

    status = TwoFactorStatusDTO.from_entity(two_factor)

    return SuccessResponse(
        data=TwoFactorStatusResponse(
            is_enabled=status.is_enabled,
            method=status.method,
            backup_codes_remaining=status.backup_codes_remaining,
            enabled_at=str(status.enabled_at) if status.enabled_at else None,
            last_used_at=str(status.last_used_at) if status.last_used_at else None,
        ),
        message="2FA status retrieved successfully",
    )


@router.post(
    "/2fa/backup-codes/regenerate",
    response_model=SuccessResponse[RegenerateBackupCodesResponse],
    summary="Regenerate backup codes",
    description="Generate new backup codes. Requires current TOTP code.",
    operation_id="regenerate_backup_codes",
)
async def regenerate_backup_codes(
    request: RegenerateBackupCodesRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """Regenerate backup codes for 2FA recovery."""
    from uuid import UUID

    use_case = RegenerateBackupCodesUseCase(
        user_repository=user_repo,
        two_factor_repository=two_factor_repo,
        totp_service=totp_svc,
    )

    result = await use_case.execute(
        user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
        totp_code=request.code,
    )

    return SuccessResponse(
        data=RegenerateBackupCodesResponse(
            backup_codes=result.backup_codes,
            previous_count=result.previous_count,
            new_count=result.new_count,
        ),
        message="Backup codes regenerated successfully. Save them securely!",
    )


@router.post(
    "/2fa/complete-login",
    response_model=SuccessResponse[AuthResponse],
    summary="Complete 2FA login",
    description="Exchange a 2FA challenge token + TOTP code for full auth tokens.",
    operation_id="complete_2fa_login",
)
async def complete_2fa_login(
    request: Complete2FALoginRequest,
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    two_factor_repo: Annotated[TwoFactorRepository, Depends(get_two_factor_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """Complete the 2FA login flow.

    Accepts the challenge token issued at /auth/login and a valid TOTP or backup code.
    Returns full auth tokens on success and sets httpOnly cookies.
    """
    use_case = CompleteTwoFactorLoginUseCase(
        user_repository=user_repo,
        two_factor_repository=two_factor_repo,
        totp_service=totp_svc,
        token_service=token_service,
    )

    result = await use_case.execute(
        challenge_token=request.challenge_token,
        code=request.code,
    )

    set_auth_cookies(
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
        message="Login successful",
    )


# ---------------------------------------------------------------------------
# Active Sessions
# ---------------------------------------------------------------------------


class SessionResponse(BaseModel):
    id: str
    device_name: str
    device_type: str
    browser: str | None
    os: str | None
    ip_address: str | None
    is_current: bool
    last_active_at: str
    created_at: str


@router.get(
    "/sessions",
    response_model=SuccessResponse[list[SessionResponse]],
    summary="List active sessions",
    operation_id="list_sessions",
)
async def list_sessions(
    request: Request,
    user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all active login sessions for the current user."""
    from src.application.services.session_service import list_active_sessions

    sessions = await list_active_sessions(db, UUID(user_id))

    # Detect current session by matching IP + user-agent
    current_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    current_ua = request.headers.get("user-agent", "")

    result = []
    for s in sessions:
        is_current = (
            s.ip_address == current_ip
            and current_ua
            and s.browser
            and s.browser.lower() in current_ua.lower()
        )
        result.append(
            SessionResponse(
                id=str(s.id),
                device_name=s.device_name,
                device_type=s.device_type,
                browser=s.browser,
                os=s.os,
                ip_address=s.ip_address,
                is_current=bool(is_current),
                last_active_at=s.last_active_at.isoformat(),
                created_at=s.created_at.isoformat(),
            )
        )

    if result and not any(s.is_current for s in result):
        result[0].is_current = True

    return SuccessResponse(data=result, message="Sessions retrieved")


class RevokeSessionRequest(BaseModel):
    session_id: str


@router.post(
    "/sessions/revoke",
    response_model=SuccessResponse[dict],
    summary="Revoke a specific session",
    operation_id="revoke_session",
)
async def revoke_session_endpoint(
    body: RevokeSessionRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Revoke a specific login session."""
    from src.application.services.session_service import revoke_session

    revoked = await revoke_session(db, UUID(body.session_id), UUID(user_id))
    await db.commit()

    if not revoked:
        raise HTTPException(status_code=404, detail="Session not found")

    return SuccessResponse(data={"revoked": True}, message="Session revoked")


@router.post(
    "/sessions/revoke-all",
    response_model=SuccessResponse[dict],
    summary="Revoke all other sessions",
    operation_id="revoke_all_sessions",
)
async def revoke_all_sessions_endpoint(
    user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Revoke all sessions except the current one."""
    from src.application.services.session_service import revoke_all_other_sessions

    count = await revoke_all_other_sessions(db, UUID(user_id))
    await db.commit()

    return SuccessResponse(
        data={"revoked_count": count},
        message=f"{count} sessions revoked",
    )

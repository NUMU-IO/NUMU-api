"""User authentication routes.

These routes handle platform user authentication (not store customers).
Tokens are set via httpOnly cookies — never exposed in JSON response body.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from src.api.dependencies import (
    get_current_user_id,
    get_password_service,
    get_token_service,
    get_user_repository,
)
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
    UpdateProfileRequest,
    UserResponse,
)
from src.api.v1.schemas.public.two_factor import (
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
)
from src.application.use_cases.auth.two_factor import (
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
from src.infrastructure.repositories import UserRepository
from src.infrastructure.repositories.two_factor_repository import (
    InMemoryTwoFactorRepository,
    get_two_factor_repository,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper to build UserResponse from auth result
# ---------------------------------------------------------------------------


def _user_response(user) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
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
):
    """Register a new platform user account.

    Tokens are set as httpOnly cookies — not included in the JSON body.
    """
    use_case = RegisterUserUseCase(
        user_repository=user_repo,
        password_service=password_service,
        token_service=token_service,
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
        data=AuthResponse(user=_user_response(result.user)),
        message="User registered successfully",
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
    response: Response,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Authenticate user and set tokens as httpOnly cookies."""
    use_case = LoginUserUseCase(
        user_repository=user_repo,
        password_service=password_service,
        token_service=token_service,
    )

    dto = LoginDTO(
        email=request.email,
        password=request.password,
    )
    result = await use_case.execute(dto)

    # Set tokens as httpOnly cookies
    set_auth_cookies(
        response,
        result.tokens.access_token,
        result.tokens.refresh_token,
    )

    return SuccessResponse(
        data=AuthResponse(user=_user_response(result.user)),
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
):
    """Get current authenticated user's profile."""
    user = await user_repo.get_by_id(user_id)

    if not user:
        raise EntityNotFoundError("User", str(user_id))

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
                trial_ends_at=str(result.trial_ends_at)
                if result.trial_ends_at
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
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
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
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
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
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
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
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
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
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
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

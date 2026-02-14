"""User authentication routes.

These routes handle platform user authentication (not store customers).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

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
from src.api.v1.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
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
from src.core.exceptions import EntityNotFoundError
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


@router.post(
    "/register",
    response_model=SuccessResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register new user",
)
async def register(
    request: RegisterRequest,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Register a new platform user account."""
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

    return SuccessResponse(
        data=AuthResponse(
            user=UserResponse(
                id=str(result.user.id),
                email=result.user.email,
                first_name=result.user.first_name,
                last_name=result.user.last_name,
                full_name=result.user.full_name,
                phone=result.user.phone,
                role=result.user.role,
                status=result.user.status,
                avatar_url=result.user.avatar_url,
                is_verified=result.user.is_verified,
                created_at=str(result.user.created_at),
                updated_at=str(result.user.updated_at),
            ),
            tokens=TokenResponse(
                access_token=result.tokens.access_token,
                refresh_token=result.tokens.refresh_token,
                token_type="bearer",
            ),
        ),
        message="User registered successfully",
    )


@router.post(
    "/login",
    response_model=SuccessResponse[AuthResponse],
    summary="Login user",
)
async def login(
    request: LoginRequest,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Authenticate user and return tokens."""
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

    return SuccessResponse(
        data=AuthResponse(
            user=UserResponse(
                id=str(result.user.id),
                email=result.user.email,
                first_name=result.user.first_name,
                last_name=result.user.last_name,
                full_name=result.user.full_name,
                phone=result.user.phone,
                role=result.user.role,
                status=result.user.status,
                avatar_url=result.user.avatar_url,
                is_verified=result.user.is_verified,
                created_at=str(result.user.created_at),
                updated_at=str(result.user.updated_at),
            ),
            tokens=TokenResponse(
                access_token=result.tokens.access_token,
                refresh_token=result.tokens.refresh_token,
                token_type="bearer",
            ),
        ),
        message="Login successful",
    )


@router.post(
    "/refresh",
    response_model=SuccessResponse[TokenResponse],
    summary="Refresh access token",
)
async def refresh_token(
    request: RefreshTokenRequest,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Refresh access token using refresh token."""
    use_case = RefreshTokenUseCase(
        user_repository=user_repo,
        token_service=token_service,
    )

    dto = RefreshTokenDTO(refresh_token=request.refresh_token)
    result = await use_case.execute(dto)

    return SuccessResponse(
        data=TokenResponse(
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            token_type="bearer",
        ),
        message="Token refreshed successfully",
    )


@router.post(
    "/forgot-password",
    response_model=SuccessResponse[MessageResponse],
    summary="Request password reset",
    description="Send password reset email to user.",
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


@router.get(
    "/me",
    response_model=SuccessResponse[UserResponse],
    summary="Get current user",
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
        ),
        message="User retrieved successfully",
    )


@router.patch(
    "/me",
    response_model=SuccessResponse[UserResponse],
    summary="Update profile",
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
# Two-Factor Authentication (2FA) Routes
# =============================================================================


@router.post(
    "/2fa/enable",
    response_model=SuccessResponse[Enable2FAResponse],
    summary="Enable 2FA",
    description="Generate TOTP secret, QR code URI, and 10 backup codes to enable 2FA.",
)
async def enable_2fa(
    user_id: Annotated[str, Depends(get_current_user_id)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """Enable Two-Factor Authentication for the current user.

    This generates:
    - A TOTP secret for authenticator apps
    - A provisioning URI (for QR code generation)
    - 10 backup codes for recovery

    The user must verify with a TOTP code to complete setup.
    Backup codes should be saved securely - they are only shown once!
    """
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
)
async def verify_2fa(
    request: Verify2FARequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
    totp_svc: Annotated[TOTPService, Depends(get_totp_service)],
):
    """Verify a 2FA code (TOTP or backup code).

    This endpoint serves two purposes:
    1. Complete 2FA setup after calling /2fa/enable (first verification)
    2. Verify 2FA during sensitive operations

    Accepts both 6-digit TOTP codes and backup codes (XXXX-XXXX format).
    """
    from uuid import UUID

    # Check if this is initial setup verification
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
    """Disable Two-Factor Authentication for the current user.

    Requires password confirmation for security. Optionally accepts
    a TOTP code for additional verification.

    WARNING: This removes all 2FA protection from the account.
    """
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
)
async def get_2fa_status(
    user_id: Annotated[str, Depends(get_current_user_id)],
    two_factor_repo: Annotated[
        InMemoryTwoFactorRepository, Depends(get_two_factor_repository)
    ],
):
    """Get the current 2FA status for the authenticated user.

    Returns whether 2FA is enabled, the method used, and
    the number of remaining backup codes.
    """
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
    """Regenerate backup codes for 2FA recovery.

    Requires a valid TOTP code to prevent abuse. All existing
    backup codes are invalidated and replaced with new ones.

    Save the new backup codes securely - they are only shown once!
    """
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

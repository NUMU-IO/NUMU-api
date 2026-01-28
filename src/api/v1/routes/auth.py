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
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from src.application.dto.auth import LoginDTO, RefreshTokenDTO, RegisterDTO
from src.application.use_cases.auth import (
    ChangePasswordDTO,
    ChangePasswordUseCase,
    LoginUserUseCase,
    RefreshTokenUseCase,
    RegisterUserUseCase,
    UpdateProfileDTO,
    UpdateProfileUseCase,
)
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.external_services import PasswordService, TokenService
from src.infrastructure.repositories import UserRepository

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

    result = await use_case.execute(refresh_token=request.refresh_token)

    return SuccessResponse(
        data=TokenResponse(
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            token_type="bearer",
            expires_in=result.expires_in,
        ),
        message="Token refreshed successfully",
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
    from uuid import UUID

    use_case = UpdateProfileUseCase(user_repository=user_repo)

    dto = UpdateProfileDTO(
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
        avatar_url=request.avatar_url,
    )

    result = await use_case.execute(
        user_id=UUID(user_id),
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
        user_id=UUID(user_id),
        dto=dto,
    )

    return SuccessResponse(
        data=MessageResponse(message="Password changed successfully"),
        message="Password changed successfully",
    )

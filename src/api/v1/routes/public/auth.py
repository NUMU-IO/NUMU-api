"""Authentication routes."""

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
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from src.application.use_cases.auth import (
    LoginUserUseCase,
    RefreshTokenUseCase,
    RegisterUserUseCase,
)
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.external_services import PasswordService, TokenService
from src.infrastructure.repositories import UserRepository

router = APIRouter(prefix="/auth", tags=["Authentication"])


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
    """Register a new user account."""
    use_case = RegisterUserUseCase(
        user_repository=user_repo,
        password_service=password_service,
        token_service=token_service,
    )
    
    result = await use_case.execute(
        email=request.email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
    )
    
    return SuccessResponse(
        data=AuthResponse(
            user=UserResponse(
                id=str(result.user.id),
                email=result.user.email,
                first_name=result.user.first_name,
                last_name=result.user.last_name,
                phone=result.user.phone,
                role=result.user.role,
                is_active=result.user.is_active,
                is_verified=result.user.is_verified,
                created_at=result.user.created_at,
            ),
            tokens=TokenResponse(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                token_type="bearer",
                expires_in=result.expires_in,
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
    
    result = await use_case.execute(
        email=request.email,
        password=request.password,
    )
    
    return SuccessResponse(
        data=AuthResponse(
            user=UserResponse(
                id=str(result.user.id),
                email=result.user.email,
                first_name=result.user.first_name,
                last_name=result.user.last_name,
                phone=result.user.phone,
                role=result.user.role,
                is_active=result.user.is_active,
                is_verified=result.user.is_verified,
                created_at=result.user.created_at,
            ),
            tokens=TokenResponse(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                token_type="bearer",
                expires_in=result.expires_in,
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
            phone=user.phone.value if user.phone else None,
            role=user.role.value,
            is_active=user.is_active,
            is_verified=user.is_verified,
            created_at=user.created_at,
        ),
        message="User retrieved successfully",
    )

"""Reset password use case."""

from src.application.dto.auth import PasswordResetDTO
from src.core.exceptions import (
    AuthenticationError,
    EntityNotFoundError,
    TokenExpiredError,
)
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService


class ResetPasswordUseCase:
    """Use case for resetting password using a token."""

    def __init__(
        self,
        user_repository: IUserRepository,
        token_service: ITokenService,
        password_service: IPasswordService,
    ) -> None:
        self.user_repository = user_repository
        self.token_service = token_service
        self.password_service = password_service

    async def execute(self, dto: PasswordResetDTO) -> None:
        """Reset user password using token."""
        try:
            # Verify token
            payload = self.token_service.verify_token(dto.token)
        except TokenExpiredError:
            raise AuthenticationError("Password reset link has expired")
        except Exception:
            raise AuthenticationError("Invalid password reset token")

        # Check token type
        if payload.token_type != "reset":
            raise AuthenticationError("Invalid token type")

        # Get user
        user = await self.user_repository.get_by_id(payload.user_id)
        if not user:
            raise EntityNotFoundError("User", str(payload.user_id))

        # Hash new password
        new_hashed_password = self.password_service.hash_password(dto.new_password)

        # Update user password
        user.hashed_password = new_hashed_password
        await self.user_repository.update(user)

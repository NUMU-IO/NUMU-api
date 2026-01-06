"""Login user use case."""

from src.application.dto.auth import AuthResponseDTO, LoginDTO, TokenDTO
from src.application.dto.user import UserDTO
from src.core.exceptions import InvalidCredentialsError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService


class LoginUserUseCase:
    """Use case for user login."""

    def __init__(
        self,
        user_repository: IUserRepository,
        password_service: IPasswordService,
        token_service: ITokenService,
    ) -> None:
        self.user_repository = user_repository
        self.password_service = password_service
        self.token_service = token_service

    async def execute(self, dto: LoginDTO) -> AuthResponseDTO:
        """Authenticate user and return auth response."""
        # Find user by email
        user = await self.user_repository.get_by_email_str(dto.email)
        if not user:
            raise InvalidCredentialsError()

        # Verify password
        if not self.password_service.verify_password(dto.password, user.hashed_password):
            raise InvalidCredentialsError()

        # Update last login
        user.update_last_login()
        await self.user_repository.update(user)

        # Generate tokens
        access_token = self.token_service.create_access_token(user)
        refresh_token = self.token_service.create_refresh_token(user)

        return AuthResponseDTO(
            user=UserDTO.from_entity(user),
            tokens=TokenDTO(
                access_token=access_token,
                refresh_token=refresh_token,
            ),
        )

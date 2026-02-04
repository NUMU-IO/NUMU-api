"""Login user use case."""

from src.application.dto.auth import AuthResponseDTO, LoginDTO, TokenDTO
from src.application.dto.user import UserDTO
from src.config.logging_config import get_logger
from src.core.exceptions import InvalidCredentialsError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService

logger = get_logger(__name__)


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
        log = logger.bind(email=dto.email)

        # Find user by email
        log.info("auth_login_attempt")
        user = await self.user_repository.get_by_email_str(dto.email)
        if not user:
            log.warning("auth_login_failed", reason="user_not_found")
            raise InvalidCredentialsError()

        log = log.bind(user_id=str(user.id), role=user.role.value)
        log.debug("auth_user_found")

        # Verify password
        if not self.password_service.verify_password(dto.password, user.hashed_password):
            log.warning("auth_login_failed", reason="invalid_password")
            raise InvalidCredentialsError()

        # Update last login
        user.update_last_login()
        await self.user_repository.update(user)

        # Generate tokens
        access_token = self.token_service.create_access_token(user)
        refresh_token = self.token_service.create_refresh_token(user)

        log.info("auth_login_success")

        return AuthResponseDTO(
            user=UserDTO.from_entity(user),
            tokens=TokenDTO(
                access_token=access_token,
                refresh_token=refresh_token,
            ),
        )

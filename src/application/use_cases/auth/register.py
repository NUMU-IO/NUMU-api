"""Register user use case."""

from datetime import UTC, datetime, timedelta

from src.application.dto.auth import AuthResponseDTO, RegisterDTO, TokenDTO
from src.application.dto.user import UserDTO
from src.config.logging_config import get_logger
from src.core.entities.user import User, UserRole, UserStatus
from src.core.exceptions import EntityAlreadyExistsError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService
from src.core.value_objects.email import Email

logger = get_logger(__name__)


class RegisterUserUseCase:
    """Use case for registering a new user."""

    def __init__(
        self,
        user_repository: IUserRepository,
        password_service: IPasswordService,
        token_service: ITokenService,
    ) -> None:
        self.user_repository = user_repository
        self.password_service = password_service
        self.token_service = token_service

    async def execute(self, dto: RegisterDTO) -> AuthResponseDTO:
        """Register a new user and return auth response."""
        log = logger.bind(email=dto.email)
        log.info("auth_register_attempt")

        email = Email(value=dto.email)

        # Check if email already exists
        if await self.user_repository.email_exists(email):
            log.warning("auth_register_failed", reason="email_exists")
            raise EntityAlreadyExistsError("User", "email", dto.email)

        # Hash password
        hashed_password = self.password_service.hash_password(dto.password)

        # Create user entity (register as store owner for merchant dashboard)
        user = User(
            email=email,
            hashed_password=hashed_password,
            first_name=dto.first_name,
            last_name=dto.last_name,
            role=UserRole.STORE_OWNER,
            status=UserStatus.PENDING_VERIFICATION,
            trial_ends_at=datetime.now(UTC) + timedelta(days=14),
        )

        # Save user
        created_user = await self.user_repository.create(user)

        log = log.bind(user_id=str(created_user.id), role=created_user.role.value)
        log.info("auth_register_success")

        # Send welcome onboarding email (non-blocking via Celery)
        try:
            from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
                send_welcome_email_task,
            )

            send_welcome_email_task.delay(
                email=dto.email,
                merchant_name=dto.first_name,
            )
        except Exception as exc:
            log.warning("welcome_email_dispatch_failed", error=str(exc))

        # Generate tokens
        access_token = self.token_service.create_access_token(created_user)
        refresh_token = self.token_service.create_refresh_token(created_user)

        return AuthResponseDTO(
            user=UserDTO.from_entity(created_user),
            tokens=TokenDTO(
                access_token=access_token,
                refresh_token=refresh_token,
            ),
        )

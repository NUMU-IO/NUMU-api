"""Register user use case."""

import random
from datetime import UTC, datetime, timedelta

from src.application.dto.auth import AuthResponseDTO, RegisterDTO, TokenDTO
from src.application.dto.user import UserDTO
from src.config.logging_config import get_logger
from src.core.entities.user import User, UserRole, UserStatus
from src.core.exceptions import EntityAlreadyExistsError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.email_service import IEmailService
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService
from src.core.validators.password import validate_password
from src.core.value_objects.email import Email
from src.infrastructure.tenancy.service import TRIAL_LIFETIME_DAYS

logger = get_logger(__name__)

# Redis key prefix for email verification codes
VERIFY_CODE_KEY_PREFIX = "email_verify_code"
VERIFY_CODE_TTL = 86400  # 24 hours


def _generate_verification_code() -> str:
    """Generate a random 6-digit verification code."""
    return f"{random.randint(0, 999999):06d}"


class RegisterUserUseCase:
    """Use case for registering a new user."""

    def __init__(
        self,
        user_repository: IUserRepository,
        password_service: IPasswordService,
        token_service: ITokenService,
        email_service: IEmailService | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.password_service = password_service
        self.token_service = token_service
        self.email_service = email_service

    async def execute(self, dto: RegisterDTO) -> AuthResponseDTO:
        """Register a new user and return auth response."""
        log = logger.bind(email=dto.email)
        log.info("auth_register_attempt")

        email = Email(value=dto.email)

        # Check if email already exists
        if await self.user_repository.email_exists(email):
            log.warning("auth_register_failed", reason="email_exists")
            raise EntityAlreadyExistsError("User", "email", dto.email)

        # Enforce password policy before hashing
        validate_password(dto.password)

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
            trial_ends_at=datetime.now(UTC) + timedelta(days=TRIAL_LIFETIME_DAYS),
        )

        # Save user
        created_user = await self.user_repository.create(user)

        log = log.bind(user_id=str(created_user.id), role=created_user.role.value)
        log.info("auth_register_success")

        # NOTE: Welcome email is now sent after email verification,
        # not at registration time. See verify_email / verify_email_code routes.

        # Send email verification link + code
        if self.email_service:
            try:
                verification_token = self.token_service.create_email_verification_token(
                    created_user
                )
                # Generate a 6-digit code and store it in Redis keyed to user id
                code = _generate_verification_code()
                log.info(
                    "verification_code_generated",
                    code=code,
                    hint="DEV ONLY — remove this log in production",
                )
                try:
                    from src.infrastructure.cache.redis_cache import RedisCacheService

                    cache = RedisCacheService()
                    await cache.set(
                        f"{VERIFY_CODE_KEY_PREFIX}:{created_user.id}",
                        code,
                        expire=VERIFY_CODE_TTL,
                    )
                except Exception as exc:
                    log.warning("verification_code_cache_failed", error=str(exc))
                    code = None  # Still send link-only email

                await self.email_service.send_verification_email(
                    email=dto.email,
                    token=verification_token,
                    code=code,
                )
            except Exception as exc:
                log.warning("verification_email_dispatch_failed", error=str(exc))

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

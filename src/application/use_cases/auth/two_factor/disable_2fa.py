"""Disable Two-Factor Authentication use case.

This use case handles disabling 2FA for a user,
requiring password verification for security.
"""

import logging
from uuid import UUID

from src.application.dto.two_factor import TwoFactorStatusDTO
from src.core.exceptions import (
    BusinessRuleViolationError,
    EntityNotFoundError,
    InvalidCredentialsError,
)
from src.core.interfaces.repositories.two_factor_repository import ITwoFactorRepository
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.totp_service import ITOTPService

logger = logging.getLogger(__name__)


class TwoFactorNotEnabledError(BusinessRuleViolationError):
    """Raised when trying to disable 2FA that is not enabled."""

    def __init__(self) -> None:
        super().__init__("Two-factor authentication is not enabled for this account")


class Disable2FAUseCase:
    """Use case for disabling Two-Factor Authentication.

    This use case:
    1. Verifies the user exists
    2. Verifies the user's password (required for security)
    3. Optionally verifies a TOTP code (additional security)
    4. Disables 2FA and clears all secrets

    Requires password confirmation to prevent unauthorized disabling
    if a session is compromised.
    """

    def __init__(
        self,
        user_repository: IUserRepository,
        two_factor_repository: ITwoFactorRepository,
        password_service: IPasswordService,
        totp_service: ITOTPService,
    ) -> None:
        """Initialize the use case.

        Args:
            user_repository: Repository for user operations
            two_factor_repository: Repository for 2FA operations
            password_service: Service for password verification
            totp_service: Service for TOTP verification
        """
        self.user_repository = user_repository
        self.two_factor_repository = two_factor_repository
        self.password_service = password_service
        self.totp_service = totp_service

    async def execute(
        self,
        user_id: UUID,
        password: str,
        totp_code: str | None = None,
    ) -> TwoFactorStatusDTO:
        """Disable 2FA for a user.

        Args:
            user_id: The UUID of the user
            password: The user's password for confirmation
            totp_code: Optional TOTP code for additional verification

        Returns:
            TwoFactorStatusDTO showing 2FA is now disabled

        Raises:
            EntityNotFoundError: If user doesn't exist
            InvalidCredentialsError: If password is incorrect
            TwoFactorNotEnabledError: If 2FA is not enabled
            BusinessRuleViolationError: If TOTP code is required but invalid
        """
        logger.info(f"Disabling 2FA for user: {user_id}")

        # 1. Verify user exists and check password
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            logger.warning(f"User not found: {user_id}")
            raise EntityNotFoundError("User", str(user_id))

        # 2. Verify password
        if not self.password_service.verify_password(password, user.hashed_password):
            logger.warning(f"Invalid password for 2FA disable: {user_id}")
            raise InvalidCredentialsError()

        # 3. Get 2FA configuration
        two_factor = await self.two_factor_repository.get_by_user_id(user_id)
        if not two_factor or not two_factor.is_enabled:
            logger.warning(f"2FA not enabled for user: {user_id}")
            raise TwoFactorNotEnabledError()

        # 4. Optionally verify TOTP code if provided
        if totp_code and two_factor.secret:
            if not self.totp_service.verify_code(two_factor.secret, totp_code):
                # Also try as backup code
                backup_valid = False
                for hashed_code in two_factor.backup_codes:
                    if self.totp_service.verify_backup_code(totp_code, hashed_code):
                        backup_valid = True
                        break

                if not backup_valid:
                    logger.warning(f"Invalid TOTP code for 2FA disable: {user_id}")
                    raise BusinessRuleViolationError("Invalid two-factor authentication code")

        # 5. Disable 2FA
        two_factor.disable()
        await self.two_factor_repository.update(two_factor)

        logger.info(f"2FA disabled for user: {user_id}")

        # 6. Return updated status
        return TwoFactorStatusDTO.from_entity(two_factor)

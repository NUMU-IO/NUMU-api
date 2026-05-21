"""Enable Two-Factor Authentication use case.

This use case handles the initial setup of 2FA for a user,
generating the TOTP secret, provisioning URI for QR code,
and 10 backup codes.
"""

import logging
from uuid import UUID

from src.application.dto.two_factor import Enable2FADTO
from src.core.entities.two_factor import TwoFactorAuth, TwoFactorMethod, TwoFactorStatus
from src.core.exceptions import (
    BusinessRuleViolationError,
    EntityNotFoundError,
)
from src.core.interfaces.repositories.two_factor_repository import ITwoFactorRepository
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.totp_service import ITOTPService

logger = logging.getLogger(__name__)


class TwoFactorAlreadyEnabledError(BusinessRuleViolationError):
    """Raised when trying to enable 2FA that is already enabled."""

    def __init__(self) -> None:
        super().__init__(
            "Two-factor authentication is already enabled for this account"
        )


class Enable2FAUseCase:
    """Use case for enabling Two-Factor Authentication.

    This use case:
    1. Verifies the user exists
    2. Checks that 2FA is not already enabled
    3. Generates a new TOTP secret
    4. Generates 10 backup codes
    5. Creates a pending TwoFactorAuth entity
    6. Returns the provisioning URI and backup codes

    The 2FA remains in 'pending' status until verified with a valid
    TOTP code using Verify2FAUseCase.
    """

    BACKUP_CODE_COUNT = 10

    def __init__(
        self,
        user_repository: IUserRepository,
        two_factor_repository: ITwoFactorRepository,
        totp_service: ITOTPService,
    ) -> None:
        """Initialize the use case.

        Args:
            user_repository: Repository for user operations
            two_factor_repository: Repository for 2FA operations
            totp_service: Service for TOTP operations
        """
        self.user_repository = user_repository
        self.two_factor_repository = two_factor_repository
        self.totp_service = totp_service

    async def execute(self, user_id: UUID) -> Enable2FADTO:
        """Enable 2FA for a user.

        Args:
            user_id: The UUID of the user enabling 2FA

        Returns:
            Enable2FADTO containing secret, QR URI, and backup codes

        Raises:
            EntityNotFoundError: If user doesn't exist
            TwoFactorAlreadyEnabledError: If 2FA is already enabled
        """
        logger.info(f"Enabling 2FA for user: {user_id}")

        # 1. Verify user exists
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            logger.warning(f"User not found: {user_id}")
            raise EntityNotFoundError("User", str(user_id))

        # 2. Check for existing 2FA
        existing_2fa = await self.two_factor_repository.get_by_user_id(user_id)
        if existing_2fa and existing_2fa.is_enabled:
            logger.warning(f"2FA already enabled for user: {user_id}")
            raise TwoFactorAlreadyEnabledError()

        # 3. Generate TOTP secret
        secret = self.totp_service.generate_secret()
        logger.debug(f"Generated TOTP secret for user: {user_id}")

        # 4. Generate provisioning URI for QR code
        provisioning_uri = self.totp_service.generate_provisioning_uri(
            secret=secret,
            email=str(user.email),
            issuer="NUMU",
        )

        # 5. Generate backup codes
        plaintext_backup_codes = self.totp_service.generate_backup_codes(
            count=self.BACKUP_CODE_COUNT
        )

        # Hash backup codes for storage
        hashed_backup_codes = [
            self.totp_service.hash_backup_code(code) for code in plaintext_backup_codes
        ]

        # 6. Create or update TwoFactorAuth entity
        if existing_2fa:
            # Update existing (was disabled or pending)
            existing_2fa.set_pending(secret, hashed_backup_codes)
            await self.two_factor_repository.update(existing_2fa)
            logger.info(f"Updated 2FA to pending for user: {user_id}")
        else:
            # Create new
            two_factor = TwoFactorAuth(
                user_id=user_id,
                method=TwoFactorMethod.TOTP,
                status=TwoFactorStatus.PENDING,
                secret=secret,
                backup_codes=hashed_backup_codes,
                backup_codes_remaining=len(hashed_backup_codes),
            )
            await self.two_factor_repository.create(two_factor)
            logger.info(f"Created pending 2FA for user: {user_id}")

        # 7. Return DTO with all setup information
        # IMPORTANT: Backup codes are only shown ONCE - user must save them!
        return Enable2FADTO(
            secret=secret,
            provisioning_uri=provisioning_uri,
            qr_code_uri=provisioning_uri,
            backup_codes=plaintext_backup_codes,
            method=TwoFactorMethod.TOTP.value,
        )

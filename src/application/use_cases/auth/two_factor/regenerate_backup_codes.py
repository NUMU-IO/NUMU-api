"""Regenerate Two-Factor Authentication backup codes use case.

This use case handles regenerating backup codes when a user
has used or lost their existing codes.
"""

import logging
from uuid import UUID

from src.application.dto.two_factor import RegenerateBackupCodesDTO
from src.core.exceptions import (
    BusinessRuleViolationError,
    EntityNotFoundError,
)
from src.core.interfaces.repositories.two_factor_repository import ITwoFactorRepository
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.totp_service import ITOTPService

logger = logging.getLogger(__name__)


class TwoFactorNotEnabledError(BusinessRuleViolationError):
    """Raised when 2FA is not enabled."""

    def __init__(self) -> None:
        super().__init__("Two-factor authentication is not enabled for this account")


class RegenerateBackupCodesUseCase:
    """Use case for regenerating 2FA backup codes.

    This use case:
    1. Verifies the user has 2FA enabled
    2. Verifies the provided TOTP code (for security)
    3. Generates new backup codes
    4. Replaces existing backup codes

    A valid TOTP code is required to regenerate backup codes
    to prevent abuse if a session is compromised.
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

    async def execute(
        self,
        user_id: UUID,
        totp_code: str,
    ) -> RegenerateBackupCodesDTO:
        """Regenerate backup codes for a user.

        Args:
            user_id: The UUID of the user
            totp_code: A valid TOTP code for verification

        Returns:
            RegenerateBackupCodesDTO with new backup codes

        Raises:
            EntityNotFoundError: If user doesn't exist
            TwoFactorNotEnabledError: If 2FA is not enabled
            BusinessRuleViolationError: If TOTP code is invalid
        """
        logger.info(f"Regenerating backup codes for user: {user_id}")

        # 1. Verify user exists
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            logger.warning(f"User not found: {user_id}")
            raise EntityNotFoundError("User", str(user_id))

        # 2. Get 2FA configuration
        two_factor = await self.two_factor_repository.get_by_user_id(user_id)
        if not two_factor or not two_factor.is_enabled:
            logger.warning(f"2FA not enabled for user: {user_id}")
            raise TwoFactorNotEnabledError()

        # 3. Verify TOTP code (required for security)
        if not two_factor.secret:
            raise TwoFactorNotEnabledError()

        if not self.totp_service.verify_code(two_factor.secret, totp_code):
            logger.warning(f"Invalid TOTP code for backup regeneration: {user_id}")
            raise BusinessRuleViolationError("Invalid two-factor authentication code")

        # 4. Generate new backup codes
        previous_count = two_factor.backup_codes_remaining

        plaintext_backup_codes = self.totp_service.generate_backup_codes(
            count=self.BACKUP_CODE_COUNT
        )

        # Hash backup codes for storage
        hashed_backup_codes = [
            self.totp_service.hash_backup_code(code)
            for code in plaintext_backup_codes
        ]

        # 5. Update entity with new backup codes
        two_factor.regenerate_backup_codes(hashed_backup_codes)
        await self.two_factor_repository.update(two_factor)

        logger.info(
            f"Regenerated backup codes for user: {user_id}, "
            f"previous: {previous_count}, new: {len(plaintext_backup_codes)}"
        )

        # 6. Return new codes (only shown once!)
        return RegenerateBackupCodesDTO(
            backup_codes=plaintext_backup_codes,
            previous_count=previous_count,
            new_count=len(plaintext_backup_codes),
        )

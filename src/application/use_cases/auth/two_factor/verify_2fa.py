"""Verify Two-Factor Authentication use case.

This use case handles verification of TOTP codes and backup codes
for both initial 2FA setup confirmation and login verification.
"""

import logging
from uuid import UUID

from src.application.dto.two_factor import Verify2FAResponseDTO
from src.core.entities.two_factor import TwoFactorStatus
from src.core.exceptions import (
    BusinessRuleViolationError,
)
from src.core.interfaces.repositories.two_factor_repository import ITwoFactorRepository
from src.core.interfaces.services.totp_service import ITOTPService

logger = logging.getLogger(__name__)


class TwoFactorNotSetupError(BusinessRuleViolationError):
    """Raised when 2FA is not set up for the user."""

    def __init__(self) -> None:
        super().__init__("Two-factor authentication is not set up for this account")


class InvalidTwoFactorCodeError(BusinessRuleViolationError):
    """Raised when the provided 2FA code is invalid."""

    def __init__(self) -> None:
        super().__init__("Invalid two-factor authentication code")


class Verify2FAUseCase:
    """Use case for verifying Two-Factor Authentication codes.

    This use case handles two scenarios:
    1. Initial verification after enabling 2FA (pending -> enabled)
    2. Verification during login or sensitive operations

    It supports both TOTP codes and backup codes.
    """

    def __init__(
        self,
        two_factor_repository: ITwoFactorRepository,
        totp_service: ITOTPService,
    ) -> None:
        """Initialize the use case.

        Args:
            two_factor_repository: Repository for 2FA operations
            totp_service: Service for TOTP operations
        """
        self.two_factor_repository = two_factor_repository
        self.totp_service = totp_service

    async def execute(
        self,
        user_id: UUID,
        code: str,
        is_initial_setup: bool = False,
    ) -> Verify2FAResponseDTO:
        """Verify a 2FA code for a user.

        Args:
            user_id: The UUID of the user
            code: The TOTP code or backup code to verify
            is_initial_setup: If True, this is the first verification to enable 2FA

        Returns:
            Verify2FAResponseDTO with verification result

        Raises:
            TwoFactorNotSetupError: If 2FA is not set up
            InvalidTwoFactorCodeError: If the code is invalid
        """
        logger.info(f"Verifying 2FA code for user: {user_id}")

        # 1. Get 2FA configuration
        two_factor = await self.two_factor_repository.get_by_user_id(user_id)
        if not two_factor:
            logger.warning(f"2FA not set up for user: {user_id}")
            raise TwoFactorNotSetupError()

        # 2. Check status
        if is_initial_setup:
            # For initial setup, must be in pending state
            if two_factor.status != TwoFactorStatus.PENDING:
                logger.warning(f"2FA not in pending state for user: {user_id}")
                raise TwoFactorNotSetupError()
        else:
            # For regular verification, must be enabled
            if not two_factor.is_enabled:
                logger.warning(f"2FA not enabled for user: {user_id}")
                raise TwoFactorNotSetupError()

        # 3. Verify the secret exists
        if not two_factor.secret:
            logger.error(f"2FA secret missing for user: {user_id}")
            raise TwoFactorNotSetupError()

        # 4. Try TOTP verification first
        normalized_code = code.replace(" ", "").replace("-", "").strip()

        if self._is_totp_format(normalized_code):
            # Looks like a TOTP code (6 digits)
            if self.totp_service.verify_code(two_factor.secret, normalized_code):
                logger.info(f"TOTP code verified for user: {user_id}")

                # If initial setup, enable 2FA
                if is_initial_setup:
                    two_factor.enable()
                    await self.two_factor_repository.update(two_factor)
                    logger.info(f"2FA enabled for user: {user_id}")
                else:
                    # Record usage
                    two_factor.record_use()
                    await self.two_factor_repository.update(two_factor)

                return Verify2FAResponseDTO(
                    verified=True,
                    method_used="totp",
                    backup_codes_remaining=two_factor.backup_codes_remaining,
                )

        # 5. Try backup code verification
        backup_result = await self._verify_backup_code(user_id, two_factor, code)
        if backup_result:
            logger.info(f"Backup code verified for user: {user_id}")

            # If initial setup, enable 2FA
            if is_initial_setup:
                two_factor.enable()
                await self.two_factor_repository.update(two_factor)
                logger.info(f"2FA enabled via backup code for user: {user_id}")

            return backup_result

        # 6. Neither TOTP nor backup code worked
        logger.warning(f"Invalid 2FA code for user: {user_id}")
        raise InvalidTwoFactorCodeError()

    def _is_totp_format(self, code: str) -> bool:
        """Check if code looks like a TOTP code (6 digits)."""
        return len(code) == 6 and code.isdigit()

    async def _verify_backup_code(
        self,
        user_id: UUID,
        two_factor,
        code: str,
    ) -> Verify2FAResponseDTO | None:
        """Try to verify and consume a backup code.

        Args:
            user_id: The user's ID
            two_factor: The TwoFactorAuth entity
            code: The backup code to try

        Returns:
            Verify2FAResponseDTO if valid, None otherwise
        """
        if not two_factor.backup_codes:
            return None

        # Check each hashed backup code
        for hashed_code in two_factor.backup_codes:
            if self.totp_service.verify_backup_code(code, hashed_code):
                # Found matching backup code - consume it
                two_factor.use_backup_code(hashed_code)
                two_factor.record_use()
                await self.two_factor_repository.update(two_factor)

                logger.info(
                    f"Backup code used for user: {user_id}, "
                    f"remaining: {two_factor.backup_codes_remaining}"
                )

                return Verify2FAResponseDTO(
                    verified=True,
                    method_used="backup_code",
                    backup_codes_remaining=two_factor.backup_codes_remaining,
                )

        return None

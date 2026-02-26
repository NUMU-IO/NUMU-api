"""Forgot password use case."""

import asyncio
import logging
import time

from src.application.dto.auth import PasswordResetRequestDTO
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.email_service import IEmailService
from src.core.interfaces.services.token_service import ITokenService

logger = logging.getLogger(__name__)

# Constant delay applied to every forgot-password request so that an
# attacker cannot distinguish "user exists" from "user does not exist"
# based on response timing.
_MIN_RESPONSE_SECONDS = 2.0


class ForgotPasswordUseCase:
    """Use case for initiating password reset."""

    def __init__(
        self,
        user_repository: IUserRepository,
        token_service: ITokenService,
        email_service: IEmailService,
    ) -> None:
        self.user_repository = user_repository
        self.token_service = token_service
        self.email_service = email_service

    async def execute(self, dto: PasswordResetRequestDTO) -> None:
        """Send password reset email if user exists.

        A constant-time delay is enforced so the caller cannot infer
        whether the email address is registered.
        """
        start = time.monotonic()

        try:
            await self._do_reset(dto)
        finally:
            elapsed = time.monotonic() - start
            remaining = _MIN_RESPONSE_SECONDS - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def _do_reset(self, dto: PasswordResetRequestDTO) -> None:
        user = await self.user_repository.get_by_email(dto.email)

        if not user:
            return

        # Generate reset token
        token = self.token_service.create_reset_token(user)

        # Send reset email - fail silently to avoid revealing user existence
        # and to handle transient email provider errors (e.g. rate limits)
        try:
            await self.email_service.send_password_reset_email(
                email=str(user.email),
                token=token,
            )
        except Exception:
            logger.exception("Failed to send password reset email to %s", user.email)

"""Verify email use case."""

from src.core.exceptions import EntityNotFoundError, InvalidTokenError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.token_service import ITokenService


class VerifyEmailUseCase:
    """Use case for verifying a user's email address."""

    def __init__(
        self,
        user_repository: IUserRepository,
        token_service: ITokenService,
    ) -> None:
        self.user_repository = user_repository
        self.token_service = token_service

    async def execute(self, token: str) -> None:
        """Verify user email using the verification token."""
        payload = self.token_service.verify_token(token)

        if payload.token_type != "email_verification":
            raise InvalidTokenError()

        user = await self.user_repository.get_by_id(payload.user_id)
        if not user:
            raise EntityNotFoundError("User", str(payload.user_id))

        if user.is_verified:
            # Already verified — idempotent, nothing to do
            return

        user.verify_email()
        await self.user_repository.update(user)

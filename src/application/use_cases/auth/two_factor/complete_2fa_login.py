"""Complete Two-Factor Authentication login use case."""

from uuid import UUID

from src.application.dto.auth import AuthResponseDTO, TokenDTO
from src.application.dto.user import UserDTO
from src.core.exceptions import EntityNotFoundError, InvalidCredentialsError
from src.core.interfaces.repositories.two_factor_repository import ITwoFactorRepository
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.totp_service import ITOTPService
from src.infrastructure.external_services.token_service import TokenService


class CompleteTwoFactorLoginUseCase:
    """Complete the login flow after first-factor (password) succeeds with 2FA required.

    Flow:
    1. Decode the short-lived challenge token issued during /login
    2. Verify the provided TOTP/backup code
    3. Issue full access + refresh tokens
    """

    def __init__(
        self,
        user_repository: IUserRepository,
        two_factor_repository: ITwoFactorRepository,
        totp_service: ITOTPService,
        token_service: TokenService,
    ) -> None:
        self.user_repository = user_repository
        self.two_factor_repository = two_factor_repository
        self.totp_service = totp_service
        self.token_service = token_service

    async def execute(self, challenge_token: str, code: str) -> AuthResponseDTO:
        """Verify 2FA code and issue real tokens.

        Args:
            challenge_token: Short-lived JWT from the /login challenge response.
            code: 6-digit TOTP or backup code.

        Returns:
            AuthResponseDTO with user info and full tokens.

        Raises:
            InvalidCredentialsError: If challenge token is invalid/expired.
            InvalidTwoFactorCodeError: If the TOTP/backup code is wrong.
        """
        # 1. Decode and validate the challenge token
        user_id: UUID | None = self.token_service.decode_challenge_token(
            challenge_token
        )
        if not user_id:
            raise InvalidCredentialsError()

        # 2. Verify the 2FA code (reuse Verify2FAUseCase logic)
        from src.application.use_cases.auth.two_factor.verify_2fa import (
            Verify2FAUseCase,
        )

        verify = Verify2FAUseCase(
            two_factor_repository=self.two_factor_repository,
            totp_service=self.totp_service,
        )
        await verify.execute(user_id=user_id, code=code, is_initial_setup=False)

        # 3. Load user
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise EntityNotFoundError("User", str(user_id))

        # 4. Record login
        user.update_last_login()
        await self.user_repository.update(user)

        # 5. Issue real tokens
        access_token = self.token_service.create_access_token(user)
        refresh_token = self.token_service.create_refresh_token(user)

        return AuthResponseDTO(
            user=UserDTO.from_entity(user),
            tokens=TokenDTO(
                access_token=access_token,
                refresh_token=refresh_token,
            ),
        )

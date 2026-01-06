"""Refresh token use case."""

from uuid import UUID

from src.application.dto.auth import RefreshTokenDTO, TokenDTO
from src.core.exceptions import EntityNotFoundError, InvalidTokenError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.token_service import ITokenService


class RefreshTokenUseCase:
    """Use case for refreshing access token."""

    def __init__(
        self,
        user_repository: IUserRepository,
        token_service: ITokenService,
    ) -> None:
        self.user_repository = user_repository
        self.token_service = token_service

    async def execute(self, dto: RefreshTokenDTO) -> TokenDTO:
        """Refresh access token using refresh token."""
        # Verify refresh token
        payload = self.token_service.verify_token(dto.refresh_token)
        
        if payload.token_type != "refresh":
            raise InvalidTokenError()

        # Get user
        user = await self.user_repository.get_by_id(payload.user_id)
        if not user:
            raise EntityNotFoundError("User", str(payload.user_id))

        # Generate new tokens
        access_token = self.token_service.create_access_token(user)
        refresh_token = self.token_service.create_refresh_token(user)

        return TokenDTO(
            access_token=access_token,
            refresh_token=refresh_token,
        )

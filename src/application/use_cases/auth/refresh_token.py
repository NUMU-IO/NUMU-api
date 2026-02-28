"""Refresh token use case — with rotation and reuse detection."""

from src.application.dto.auth import RefreshTokenDTO, TokenDTO
from src.application.services.refresh_token_blacklist_service import (
    RefreshTokenBlacklistService,
)
from src.config.logging_config import get_logger
from src.core.exceptions import EntityNotFoundError, InvalidTokenError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.token_service import ITokenService

logger = get_logger(__name__)


class RefreshTokenUseCase:
    """Use case for refreshing access token with automatic rotation.

    Each refresh token can only be used once (identified by its jti claim).
    Re-using a consumed token is treated as a sign of theft and logged
    as a security event.
    """

    def __init__(
        self,
        user_repository: IUserRepository,
        token_service: ITokenService,
        blacklist_service: RefreshTokenBlacklistService,
    ) -> None:
        self.user_repository = user_repository
        self.token_service = token_service
        self.blacklist_service = blacklist_service

    async def execute(self, dto: RefreshTokenDTO) -> TokenDTO:
        """Refresh access token using refresh token."""
        # Verify refresh token signature and expiry
        payload = self.token_service.verify_token(dto.refresh_token)

        if payload.token_type != "refresh":
            raise InvalidTokenError()

        # Detect token reuse — potential theft
        if payload.jti and await self.blacklist_service.is_used(payload.jti):
            logger.warning(
                "refresh_token_reuse_detected",
                user_id=str(payload.user_id),
                jti=payload.jti,
            )
            raise InvalidTokenError()

        # Get user
        user = await self.user_repository.get_by_id(payload.user_id)
        if not user:
            raise EntityNotFoundError("User", str(payload.user_id))

        # Blacklist the consumed jti before issuing new tokens
        if payload.jti:
            await self.blacklist_service.mark_used(payload.jti, payload.exp)

        # Issue fresh token pair (new jti on the new refresh token)
        access_token = self.token_service.create_access_token(user)
        refresh_token = self.token_service.create_refresh_token(user)

        logger.info("refresh_token_rotated", user_id=str(user.id))

        return TokenDTO(
            access_token=access_token,
            refresh_token=refresh_token,
        )

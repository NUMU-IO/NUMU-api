"""Change password use case."""

import time
from dataclasses import dataclass
from uuid import UUID

from src.application.services.token_revocation_service import TokenRevocationService
from src.core.exceptions import AuthenticationError, EntityNotFoundError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.password_service import IPasswordService


@dataclass
class ChangePasswordDTO:
    """Change password data transfer object."""

    current_password: str
    new_password: str


class ChangePasswordUseCase:
    """Use case for changing user password."""

    def __init__(
        self,
        user_repository: IUserRepository,
        password_service: IPasswordService,
        revocation_service: TokenRevocationService,
    ) -> None:
        self.user_repository = user_repository
        self.password_service = password_service
        self.revocation_service = revocation_service

    async def execute(
        self,
        user_id: UUID,
        dto: ChangePasswordDTO,
    ) -> None:
        """Change user password and revoke all existing sessions."""
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise EntityNotFoundError("User", str(user_id))

        # Verify current password
        if not await self.password_service.verify_password(
            dto.current_password, user.hashed_password
        ):
            raise AuthenticationError("Current password is incorrect")

        # Hash new password
        new_hashed_password = await self.password_service.hash_password(
            dto.new_password
        )

        # Update password
        user.hashed_password = new_hashed_password
        user.touch()

        await self.user_repository.update(user)

        # Revoke all tokens issued before now so existing sessions can't be reused
        await self.revocation_service.revoke_all(user_id, int(time.time()))

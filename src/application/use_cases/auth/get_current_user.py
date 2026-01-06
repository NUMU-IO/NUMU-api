"""Get current user use case."""

from uuid import UUID

from src.application.dto.user import UserDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.user_repository import IUserRepository


class GetCurrentUserUseCase:
    """Use case for getting current authenticated user."""

    def __init__(self, user_repository: IUserRepository) -> None:
        self.user_repository = user_repository

    async def execute(self, user_id: UUID) -> UserDTO:
        """Get current user by ID."""
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise EntityNotFoundError("User", str(user_id))
        return UserDTO.from_entity(user)

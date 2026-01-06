"""Delete store use case."""

from uuid import UUID

from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.store_repository import IStoreRepository


class DeleteStoreUseCase:
    """Use case for deleting a store."""

    def __init__(self, store_repository: IStoreRepository) -> None:
        self.store_repository = store_repository

    async def execute(self, store_id: UUID, user_id: UUID) -> bool:
        """Delete a store."""
        # Get store
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        # Check ownership
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to delete this store")

        # Delete store
        return await self.store_repository.delete(store_id)

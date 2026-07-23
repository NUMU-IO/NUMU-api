"""Delete category use case."""

from uuid import UUID

from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class DeleteCategoryUseCase:
    """Use case for deleting a category."""

    def __init__(
        self,
        category_repository: ICategoryRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.category_repository = category_repository
        self.store_repository = store_repository

    async def execute(self, category_id: UUID, user_id: UUID, store_id: UUID) -> bool:
        # store_id REQUIRED — foreign category is not-found, never forbidden
        # (CL-1 cross-store write, 2026-07-21).
        category = await self.category_repository.get_by_id(category_id)
        if not category or category.store_id != store_id:
            raise EntityNotFoundError("Category", str(category_id))

        store = await self.store_repository.get_by_id(category.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to delete this category"
            )

        return await self.category_repository.delete(category_id)

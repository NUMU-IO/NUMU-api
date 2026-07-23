"""Get category use case."""

from uuid import UUID

from src.application.dto.category import CategoryDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.category_repository import ICategoryRepository


class GetCategoryUseCase:
    """Use case for getting a category."""

    def __init__(self, category_repository: ICategoryRepository) -> None:
        self.category_repository = category_repository

    async def execute(self, category_id: UUID, store_id: UUID) -> CategoryDTO:
        """Get a category by ID, scoped to the store that owns it.

        `store_id` is REQUIRED — the same cross-tenant read that affected
        products applied here (CL-1, verified 2026-07-21): the route
        authorises the path store, but the lookup ignored it, so any
        authenticated merchant could read any category on the platform.

        A foreign category is reported as not-found, never as forbidden, so
        the endpoint cannot be used to probe which ids exist.
        """
        category = await self.category_repository.get_by_id(category_id)
        if not category or category.store_id != store_id:
            raise EntityNotFoundError("Category", str(category_id))
        return CategoryDTO.from_entity(category)

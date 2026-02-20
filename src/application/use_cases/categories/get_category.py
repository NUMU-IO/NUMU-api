"""Get category use case."""

from uuid import UUID

from src.application.dto.category import CategoryDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.category_repository import ICategoryRepository


class GetCategoryUseCase:
    """Use case for getting a category."""

    def __init__(self, category_repository: ICategoryRepository) -> None:
        self.category_repository = category_repository

    async def execute(self, category_id: UUID) -> CategoryDTO:
        category = await self.category_repository.get_by_id(category_id)
        if not category:
            raise EntityNotFoundError("Category", str(category_id))
        return CategoryDTO.from_entity(category)

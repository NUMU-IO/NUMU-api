"""List categories use case."""

from uuid import UUID

from src.application.dto.category import CategoryDTO
from src.core.interfaces.repositories.category_repository import ICategoryRepository


class ListCategoriesUseCase:
    """Use case for listing categories."""

    def __init__(self, category_repository: ICategoryRepository) -> None:
        self.category_repository = category_repository

    async def execute(
        self,
        store_id: UUID,
        include_inactive: bool = False,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CategoryDTO]:
        categories = await self.category_repository.get_by_store(
            store_id=store_id,
            skip=skip,
            limit=limit,
            include_inactive=include_inactive,
        )

        # Get product counts
        product_counts = await self.category_repository.get_product_counts(store_id)

        return [
            CategoryDTO.from_entity(c, product_count=product_counts.get(c.id, 0))
            for c in categories
        ]

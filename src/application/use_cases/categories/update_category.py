"""Update category use case."""

from uuid import UUID

from slugify import slugify

from src.application.dto.category import CategoryDTO, UpdateCategoryDTO
from src.core.exceptions import (
    AuthorizationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class UpdateCategoryUseCase:
    """Use case for updating a category."""

    def __init__(
        self,
        category_repository: ICategoryRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.category_repository = category_repository
        self.store_repository = store_repository

    async def execute(
        self,
        category_id: UUID,
        dto: UpdateCategoryDTO,
        user_id: UUID,
    ) -> CategoryDTO:
        category = await self.category_repository.get_by_id(category_id)
        if not category:
            raise EntityNotFoundError("Category", str(category_id))

        store = await self.store_repository.get_by_id(category.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to update this category"
            )

        if dto.name is not None:
            category.name = dto.name.strip()

        if dto.slug is not None:
            new_slug = dto.slug.strip()
            if new_slug != category.slug:
                existing = await self.category_repository.get_by_slug(
                    category.store_id, new_slug
                )
                if existing:
                    raise EntityAlreadyExistsError("Category", "slug", new_slug)
                category.slug = new_slug
        elif dto.name is not None:
            # Auto-update slug when name changes
            new_slug = slugify(
                dto.name, allow_unicode=True
            ) or dto.name.lower().replace(" ", "-")
            if new_slug != category.slug:
                existing = await self.category_repository.get_by_slug(
                    category.store_id, new_slug
                )
                if not existing:
                    category.slug = new_slug

        if dto.description is not None:
            category.description = dto.description
        if dto.image_url is not None:
            category.image_url = dto.image_url
        if dto.parent_id is not None:
            if dto.parent_id == category.id:
                raise ValidationError("Category cannot be its own parent")
            parent = await self.category_repository.get_by_id(dto.parent_id)
            if not parent or parent.store_id != category.store_id:
                raise ValidationError("Parent category not found in this store")
            category.parent_id = dto.parent_id
        if dto.position is not None:
            category.position = dto.position
        if dto.is_active is not None:
            category.is_active = dto.is_active
        if dto.extra_data is not None:
            category.update_metadata(**dto.extra_data)

        category.touch()
        updated = await self.category_repository.update(category)
        return CategoryDTO.from_entity(updated)

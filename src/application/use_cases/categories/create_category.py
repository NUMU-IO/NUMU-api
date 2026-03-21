"""Create category use case."""

from uuid import UUID

from slugify import slugify

from src.application.dto.category import CategoryDTO, CreateCategoryDTO
from src.core.entities.category import Category
from src.core.exceptions import (
    AuthorizationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class CreateCategoryUseCase:
    """Use case for creating a new category."""

    def __init__(
        self,
        category_repository: ICategoryRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.category_repository = category_repository
        self.store_repository = store_repository

    async def execute(
        self,
        dto: CreateCategoryDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> CategoryDTO:
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to create categories for this store"
            )

        if not dto.name or not dto.name.strip():
            raise ValidationError("Category name is required")

        slug = (
            dto.slug
            or slugify(dto.name, allow_unicode=True)
            or dto.name.lower().replace(" ", "-")
        )

        # Ensure slug uniqueness within the store
        existing = await self.category_repository.get_by_slug(store_id, slug)
        if existing:
            slug = f"{slug}-{str(store_id)[:4]}"
            existing = await self.category_repository.get_by_slug(store_id, slug)
            if existing:
                raise EntityAlreadyExistsError("Category", "slug", slug)

        # Validate parent exists if specified
        if dto.parent_id:
            parent = await self.category_repository.get_by_id(dto.parent_id)
            if not parent or parent.store_id != store_id:
                raise ValidationError("Parent category not found in this store")

        category = Category(
            store_id=store_id,
            tenant_id=store.tenant_id,
            name=dto.name.strip(),
            slug=slug,
            description=dto.description,
            image_url=dto.image_url,
            parent_id=dto.parent_id,
            position=dto.position,
            is_active=dto.is_active,
            metadata=dto.extra_data or {},
        )

        created = await self.category_repository.create(category)
        return CategoryDTO.from_entity(created)

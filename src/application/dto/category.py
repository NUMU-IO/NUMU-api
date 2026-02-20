"""Category DTOs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.category import Category


@dataclass
class CategoryDTO(BaseDTO):
    """Category data transfer object."""

    id: UUID
    store_id: UUID
    name: str
    slug: str
    description: str | None
    image_url: str | None
    parent_id: UUID | None
    position: int
    is_active: bool
    metadata: dict[str, Any]
    product_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: Category, product_count: int = 0) -> "CategoryDTO":
        """Create DTO from Category entity."""
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            name=entity.name,
            slug=entity.slug,
            description=entity.description,
            image_url=entity.image_url,
            parent_id=entity.parent_id,
            position=entity.position,
            is_active=entity.is_active,
            metadata=entity.metadata,
            product_count=product_count,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateCategoryDTO(BaseDTO):
    """Create category data transfer object."""

    name: str
    slug: str | None = None
    description: str | None = None
    image_url: str | None = None
    parent_id: UUID | None = None
    position: int = 0
    is_active: bool = True


@dataclass
class UpdateCategoryDTO(BaseDTO):
    """Update category data transfer object."""

    name: str | None = None
    slug: str | None = None
    description: str | None = None
    image_url: str | None = None
    parent_id: UUID | None = None
    position: int | None = None
    is_active: bool | None = None

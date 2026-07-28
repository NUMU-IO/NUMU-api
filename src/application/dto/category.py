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
    seo_title: str | None
    seo_description: str | None
    social_image_url: str | None
    template_suffix: str | None
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
            seo_title=entity.seo_title,
            seo_description=entity.seo_description,
            social_image_url=entity.social_image_url,
            template_suffix=entity.template_suffix,
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
    seo_title: str | None = None
    seo_description: str | None = None
    social_image_url: str | None = None
    # Alternate template variant suffix (Shopify-style); null = base template.
    template_suffix: str | None = None
    extra_data: dict[str, Any] | None = None


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
    # Alternate template variant suffix (Shopify-style); null = base template.
    # `template_suffix_provided` carries whether the client sent the key (route
    # derives it from the request's ``model_fields_set``) so a partial PATCH can
    # clear the override via an explicit null without wiping it when omitted.
    seo_title: str | None = None
    seo_description: str | None = None
    social_image_url: str | None = None
    template_suffix: str | None = None
    template_suffix_provided: bool = False
    extra_data: dict[str, Any] | None = None

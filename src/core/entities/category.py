"""Category entity for product categorization."""

from datetime import datetime
from uuid import UUID

from src.core.entities.base import BaseEntity


class Category(BaseEntity):
    """Category entity for product categorization."""

    def __init__(
        self,
        store_id: UUID,
        name: str,
        slug: str,
        description: str | None = None,
        image_url: str | None = None,
        parent_id: UUID | None = None,
        position: int = 0,
        is_active: bool = True,
        metadata: dict | None = None,
        id: UUID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(id=id, created_at=created_at, updated_at=updated_at)
        self.store_id = store_id
        self.name = name
        self.slug = slug
        self.description = description
        self.image_url = image_url
        self.parent_id = parent_id
        self.position = position
        self.is_active = is_active
        self.metadata = metadata or {}

"""Category entity for product categorization."""

from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class Category(BaseEntity):
    """Category entity for product categorization.

    Categories support hierarchical structures through parent_id,
    allowing for nested category trees.
    """

    store_id: UUID
    name: str
    slug: str
    description: str | None = None
    image_url: str | None = None
    parent_id: UUID | None = None
    position: int = Field(default=0, ge=0)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_root(self) -> bool:
        """Check if this is a root category (no parent)."""
        return self.parent_id is None

    @property
    def has_parent(self) -> bool:
        """Check if this category has a parent."""
        return self.parent_id is not None

    def activate(self) -> None:
        """Activate the category."""
        self.is_active = True
        self.touch()

    def deactivate(self) -> None:
        """Deactivate the category."""
        self.is_active = False
        self.touch()

    def set_parent(self, parent_id: UUID | None) -> None:
        """Set the parent category.

        Args:
            parent_id: The parent category ID, or None to make this a root category
        """
        self.parent_id = parent_id
        self.touch()

    def update_position(self, position: int) -> None:
        """Update the category position for ordering.

        Args:
            position: New position value (lower = earlier in order)
        """
        if position < 0:
            raise ValueError("Position cannot be negative")
        self.position = position
        self.touch()

    def update_metadata(self, **kwargs: Any) -> None:
        """Update category metadata.

        Args:
            **kwargs: Key-value pairs to update in metadata
        """
        self.metadata.update(kwargs)
        self.touch()

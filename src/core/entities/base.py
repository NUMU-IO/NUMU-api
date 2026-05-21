"""Base entity class for all domain entities."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class BaseEntity(BaseModel):
    """Base class for all domain entities.

    All entities inherit from this class which provides:
    - Unique identifier (UUID)
    - Creation timestamp
    - Update timestamp
    - Equality based on ID
    - Hashability for use in sets and dicts

    Entities are mutable by default (validate_assignment=True allows updates).
    Use ConfigDict(frozen=True) in subclasses for immutable entities.
    """

    model_config = ConfigDict(
        # Enable validation on attribute assignment
        validate_assignment=True,
        # Allow creation from ORM models
        from_attributes=True,
        # Use enum values instead of enum instances in serialization
        use_enum_values=False,
        # Allow population by field name
        populate_by_name=True,
    )

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def __eq__(self, other: Any) -> bool:
        """Entities are equal if they have the same ID."""
        if not isinstance(other, BaseEntity):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash based on ID for use in sets and dicts."""
        return hash(self.id)

    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"<{self.__class__.__name__}(id={self.id})>"

    def touch(self) -> None:
        """Update the updated_at timestamp to current time."""
        self.updated_at = datetime.now(UTC)

    def model_dump_for_db(self, **kwargs: Any) -> dict[str, Any]:
        """Dump model for database insertion.

        This method ensures consistent serialization for database operations.
        Override in subclasses to handle specific field transformations.
        """
        return self.model_dump(**kwargs)

"""Store DTOs."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.store import Store


@dataclass
class StoreDTO(BaseDTO):
    """Store data transfer object."""

    id: UUID
    name: str
    slug: str
    owner_id: UUID
    description: str | None
    logo_url: str | None
    banner_url: str | None
    status: str
    default_currency: str
    contact_email: str | None
    contact_phone: str | None
    address: dict
    social_links: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: Store) -> "StoreDTO":
        """Create DTO from Store entity."""
        return cls(
            id=entity.id,
            name=entity.name,
            slug=entity.slug,
            owner_id=entity.owner_id,
            description=entity.description,
            logo_url=entity.logo_url,
            banner_url=entity.banner_url,
            status=entity.status.value,
            default_currency=entity.default_currency.value,
            contact_email=entity.contact_email,
            contact_phone=entity.contact_phone,
            address=entity.address,
            social_links=entity.social_links,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateStoreDTO(BaseDTO):
    """Create store data transfer object."""

    name: str
    slug: str | None = None
    description: str | None = None
    default_currency: str = "USD"
    contact_email: str | None = None
    contact_phone: str | None = None


@dataclass
class UpdateStoreDTO(BaseDTO):
    """Update store data transfer object."""

    name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    address: dict | None = None
    social_links: dict | None = None
    settings: dict | None = None

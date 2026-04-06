"""Store DTOs."""

from dataclasses import dataclass
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
    subdomain: str | None
    custom_domain: str | None
    store_url: str
    owner_id: UUID
    description: str | None
    logo_url: str | None
    banner_url: str | None
    status: str
    default_currency: str
    default_language: str
    contact_email: str | None
    contact_phone: str | None
    address: dict
    social_links: dict
    settings: dict
    theme_settings: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: Store) -> "StoreDTO":
        """Create DTO from Store entity."""
        return cls(
            id=entity.id,
            name=entity.name,
            slug=entity.slug,
            subdomain=entity.subdomain,
            custom_domain=entity.custom_domain,
            store_url=entity.store_url,
            owner_id=entity.owner_id,
            description=entity.description,
            logo_url=entity.logo_url,
            banner_url=entity.banner_url,
            status=entity.status.value,
            default_currency=entity.default_currency.value,
            default_language=entity.default_language,
            contact_email=entity.contact_email,
            contact_phone=entity.contact_phone,
            address=entity.address,
            social_links=entity.social_links,
            settings=entity.settings or {},
            theme_settings=entity.theme_settings,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateStoreDTO(BaseDTO):
    """Create store data transfer object."""

    name: str
    subdomain: (
        str  # Required - the store's subdomain (e.g., "mystore" for mystore.numu.io)
    )
    slug: str | None = None
    description: str | None = None
    default_currency: str = "EGP"
    default_language: str = "ar"
    contact_email: str | None = None
    contact_phone: str | None = None
    invite_code: str | None = None


@dataclass
class UpdateStoreDTO(BaseDTO):
    """Update store data transfer object."""

    name: str | None = None
    default_language: str | None = None
    description: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    address: dict | None = None
    social_links: dict | None = None
    status: str | None = None
    settings: dict | None = None
    theme_settings: dict | None = None

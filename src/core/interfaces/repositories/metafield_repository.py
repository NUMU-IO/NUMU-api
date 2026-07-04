"""Metafield repository interfaces."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.metafield import (
    MetafieldDefinition,
    MetafieldOwnerType,
    MetafieldValue,
    ResolvedMetafield,
)
from src.core.interfaces.repositories.base import BaseRepository


class IMetafieldDefinitionRepository(BaseRepository[MetafieldDefinition]):
    """Metafield definition (typed field schema) repository interface."""

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        owner_type: MetafieldOwnerType | None = None,
    ) -> list[MetafieldDefinition]:
        """List definitions for a store, optionally filtered by owner type."""
        ...

    @abstractmethod
    async def get_by_key(
        self,
        store_id: UUID,
        owner_type: MetafieldOwnerType,
        namespace: str,
        key: str,
    ) -> MetafieldDefinition | None:
        """Resolve a single definition by its ``namespace.key`` address."""
        ...


class IMetafieldValueRepository(BaseRepository[MetafieldValue]):
    """Metafield value (per-owner concrete data) repository interface."""

    @abstractmethod
    async def get_for_owner(
        self, store_id: UUID, owner_id: UUID
    ) -> list[MetafieldValue]:
        """List all raw values set on a single owner."""
        ...

    @abstractmethod
    async def get_by_definition_and_owner(
        self, definition_id: UUID, owner_id: UUID
    ) -> MetafieldValue | None:
        """Get the single value for a (definition, owner) pair."""
        ...

    @abstractmethod
    async def list_public_for_owner(
        self,
        store_id: UUID,
        owner_type: MetafieldOwnerType,
        owner_id: UUID,
    ) -> list[ResolvedMetafield]:
        """List public metafields for an owner, typed for storefront output."""
        ...

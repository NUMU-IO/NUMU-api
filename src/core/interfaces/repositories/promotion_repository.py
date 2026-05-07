"""Promotion repository protocols.

These are pure interfaces — no SQLAlchemy. The infrastructure layer
(step 04) supplies concrete implementations.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.value_objects.localized_promotion_content import (
    LocalizedPromotionContent,
)


class IPromotionRepository(ABC):
    """Persistence for the `Promotion` aggregate root."""

    @abstractmethod
    async def create(self, promotion: Promotion) -> Promotion: ...

    @abstractmethod
    async def get_by_id(
        self, store_id: UUID, promotion_id: UUID
    ) -> Promotion | None: ...

    @abstractmethod
    async def list_for_store(
        self,
        store_id: UUID,
        *,
        status: PromotionStatus | None = None,
        surface: PromotionSurface | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Promotion], int]:
        """Return (rows, total_count). For merchant-side admin lists."""

    @abstractmethod
    async def list_active_for_storefront(
        self, store_id: UUID, now: datetime
    ) -> list[Promotion]:
        """Active promos with displays/targets prefetched, for resolver."""

    @abstractmethod
    async def update(self, promotion: Promotion) -> Promotion:
        """Update with optimistic locking on `version`."""

    @abstractmethod
    async def delete(self, store_id: UUID, promotion_id: UUID) -> None: ...


class IPromotionDisplayRepository(ABC):
    """Persistence for the `PromotionDisplay` child entity."""

    @abstractmethod
    async def list_for_promotion(
        self, promotion_id: UUID
    ) -> list[PromotionDisplay]: ...

    @abstractmethod
    async def replace_for_promotion(
        self, promotion_id: UUID, displays: list[PromotionDisplay]
    ) -> list[PromotionDisplay]:
        """Replace the full set atomically."""


class IPromotionTargetRepository(ABC):
    """Persistence for the `PromotionTarget` child entity."""

    @abstractmethod
    async def list_for_promotion(self, promotion_id: UUID) -> list[PromotionTarget]: ...

    @abstractmethod
    async def replace_for_promotion(
        self, promotion_id: UUID, targets: list[PromotionTarget]
    ) -> list[PromotionTarget]: ...


class IPromotionTranslationRepository(ABC):
    """Persistence for the `LocalizedPromotionContent` per locale."""

    @abstractmethod
    async def get_for_promotion(
        self, promotion_id: UUID
    ) -> dict[str, LocalizedPromotionContent]: ...

    @abstractmethod
    async def replace_for_promotion(
        self,
        promotion_id: UUID,
        tenant_id: UUID,
        translations: dict[str, LocalizedPromotionContent],
    ) -> None: ...

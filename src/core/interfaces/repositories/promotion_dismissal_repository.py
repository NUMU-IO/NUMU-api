"""PromotionDismissal repository protocol."""

from abc import ABC, abstractmethod
from uuid import UUID

from src.core.entities.promotion_dismissal import PromotionDismissal


class IPromotionDismissalRepository(ABC):
    """Per-customer / per-visitor promotion suppression."""

    @abstractmethod
    async def record(self, dismissal: PromotionDismissal) -> PromotionDismissal:
        """Insert a dismissal. Idempotent on (promotion_id, subject)."""

    @abstractmethod
    async def list_dismissed_promotion_ids(
        self,
        store_id: UUID,
        *,
        customer_id: UUID | None = None,
        visitor_token: str | None = None,
    ) -> set[UUID]:
        """Return promotion_ids the given subject has dismissed."""

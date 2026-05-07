"""RecordPromotionEventUseCase."""

from typing import Any
from uuid import UUID

from src.core.entities.promotion_event import PromotionEvent
from src.core.enums.promotion_enums import PromotionEventType
from src.core.exceptions.promotion_exceptions import PromotionNotFound
from src.core.interfaces.repositories.promotion_event_repository import (
    IPromotionEventRepository,
)
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
)


class RecordPromotionEventUseCase:
    """Append a single analytics event row.

    Heavy aggregation lives in step 13. Rate-limiting per
    (session_id, promotion_id, event_type) is enforced at the API layer.
    """

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        event_repo: IPromotionEventRepository,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._event_repo = event_repo

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        event_type: PromotionEventType,
        customer_id: UUID | None = None,
        session_id: str | None = None,
        order_id: UUID | None = None,
        discount_amount_cents: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Cheap validation that the promotion belongs to the store.
        promo = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if promo is None or promo.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))

        event = PromotionEvent(
            tenant_id=tenant_id,
            store_id=store_id,
            promotion_id=promotion_id,
            event_type=event_type,
            customer_id=customer_id,
            session_id=session_id,
            order_id=order_id,
            discount_amount_cents=discount_amount_cents,
            metadata=metadata or {},
        )
        await self._event_repo.record(event)

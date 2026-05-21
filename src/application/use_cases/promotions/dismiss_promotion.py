"""DismissPromotionUseCase — record a per-subject suppression."""

from uuid import UUID

from src.core.entities.promotion_dismissal import PromotionDismissal
from src.core.entities.promotion_event import PromotionEvent
from src.core.exceptions.promotion_exceptions import PromotionNotFound
from src.core.interfaces.repositories.promotion_dismissal_repository import (
    IPromotionDismissalRepository,
)
from src.core.interfaces.repositories.promotion_event_repository import (
    IPromotionEventRepository,
)
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionRepository,
)


class DismissPromotionUseCase:
    """Idempotent suppression. Also writes a `dismiss` analytics event."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        dismissal_repo: IPromotionDismissalRepository,
        event_repo: IPromotionEventRepository,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._dismissal_repo = dismissal_repo
        self._event_repo = event_repo

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        customer_id: UUID | None = None,
        visitor_token: str | None = None,
    ) -> None:
        if (customer_id is None) == (visitor_token is None):
            raise ValueError(
                "exactly one of customer_id or visitor_token must be provided"
            )

        promo = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if promo is None or promo.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))

        dismissal = PromotionDismissal(
            tenant_id=tenant_id,
            promotion_id=promotion_id,
            customer_id=customer_id,
            visitor_token=visitor_token,
        )
        await self._dismissal_repo.record(dismissal)
        await self._event_repo.record(
            PromotionEvent.dismiss(
                tenant_id=tenant_id,
                store_id=store_id,
                promotion_id=promotion_id,
                customer_id=customer_id,
                session_id=visitor_token,
            )
        )

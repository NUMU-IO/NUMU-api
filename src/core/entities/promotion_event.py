"""PromotionEvent — append-only analytics record."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums.promotion_enums import PromotionEventType


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PromotionEvent(BaseModel):
    """Single immutable analytics row for a promotion.

    Frozen — events are append-only. Use the classmethod factories to
    build the right shape for each event_type.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True, populate_by_name=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    store_id: UUID
    promotion_id: UUID
    event_type: PromotionEventType
    customer_id: UUID | None = None
    session_id: str | None = None
    order_id: UUID | None = None
    discount_amount_cents: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_utc_now)

    # ------------------------------------------------------------------ #
    # Factories                                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def impression(
        cls,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        customer_id: UUID | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "PromotionEvent":
        return cls(
            tenant_id=tenant_id,
            store_id=store_id,
            promotion_id=promotion_id,
            event_type=PromotionEventType.IMPRESSION,
            customer_id=customer_id,
            session_id=session_id,
            metadata=metadata or {},
        )

    @classmethod
    def click(
        cls,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        customer_id: UUID | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "PromotionEvent":
        return cls(
            tenant_id=tenant_id,
            store_id=store_id,
            promotion_id=promotion_id,
            event_type=PromotionEventType.CLICK,
            customer_id=customer_id,
            session_id=session_id,
            metadata=metadata or {},
        )

    @classmethod
    def dismiss(
        cls,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        customer_id: UUID | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "PromotionEvent":
        return cls(
            tenant_id=tenant_id,
            store_id=store_id,
            promotion_id=promotion_id,
            event_type=PromotionEventType.DISMISS,
            customer_id=customer_id,
            session_id=session_id,
            metadata=metadata or {},
        )

    @classmethod
    def redeem(
        cls,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        order_id: UUID,
        discount_amount_cents: int,
        customer_id: UUID | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "PromotionEvent":
        return cls(
            tenant_id=tenant_id,
            store_id=store_id,
            promotion_id=promotion_id,
            event_type=PromotionEventType.REDEEM,
            order_id=order_id,
            discount_amount_cents=discount_amount_cents,
            customer_id=customer_id,
            session_id=session_id,
            metadata=metadata or {},
        )

    @classmethod
    def convert(
        cls,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        order_id: UUID,
        customer_id: UUID | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "PromotionEvent":
        return cls(
            tenant_id=tenant_id,
            store_id=store_id,
            promotion_id=promotion_id,
            event_type=PromotionEventType.CONVERT,
            order_id=order_id,
            customer_id=customer_id,
            session_id=session_id,
            metadata=metadata or {},
        )

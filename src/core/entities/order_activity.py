"""Order activity entity — staff comment or persisted system event on an order."""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from src.core.entities.base import BaseEntity


class OrderActivityKind(StrEnum):
    """Kind of order activity row."""

    COMMENT = "comment"
    SYSTEM_EVENT = "system_event"


class OrderActivity(BaseEntity):
    """Per-order activity entry visible in the merchant-hub timeline."""

    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
        use_enum_values=False,
        populate_by_name=True,
    )

    order_id: UUID
    store_id: UUID
    tenant_id: UUID | None = None
    user_id: UUID | None = None

    kind: OrderActivityKind = OrderActivityKind.SYSTEM_EVENT
    event_type: str | None = None
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

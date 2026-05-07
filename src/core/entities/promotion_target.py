"""PromotionTarget entity — who a promotion applies to."""

from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity
from src.core.enums.promotion_enums import TargetKind


class PromotionTarget(BaseEntity):
    """Audience / catalog / geo rule attached to a promotion.

    A promotion is eligible iff every `inclusion=True` rule matches AND
    no `inclusion=False` rule matches. Empty target set = applies to
    everyone / everything. The shape of `target_value` depends on
    `target_kind`; see `services.promotion_eligibility_checker` for the
    matching logic.
    """

    tenant_id: UUID
    promotion_id: UUID
    target_kind: TargetKind
    target_value: dict[str, Any] = Field(default_factory=dict)
    inclusion: bool = True

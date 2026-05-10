"""PromotionTarget entity — who a promotion applies to."""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity
from src.core.enums.promotion_enums import TargetKind

# Roles a target can play in BOGO targeting. `None` (default) keeps the
# legacy semantics: a global eligibility filter checked by
# PromotionEligibilityChecker. Non-null roles are read by the discount
# calculator when filtering cart lines for "customer buys X / customer
# gets Y" BOGO rules — they don't gate eligibility, only restrict which
# lines participate in the rule.
TargetRole = Literal["buy_set", "get_set"]


class PromotionTarget(BaseEntity):
    """Audience / catalog / geo rule attached to a promotion.

    A promotion is eligible iff every `inclusion=True` rule (with
    `role=None`) matches AND no `inclusion=False` rule matches. Empty
    target set = applies to everyone / everything. Role-tagged targets
    (`role="buy_set" | "get_set"`) bypass the eligibility checker and
    feed the BOGO discount calculator. The shape of `target_value`
    depends on `target_kind`; see `services.promotion_eligibility_checker`
    and `services.discount_calculator` for the matching logic.
    """

    tenant_id: UUID
    promotion_id: UUID
    target_kind: TargetKind
    target_value: dict[str, Any] = Field(default_factory=dict)
    inclusion: bool = True
    role: TargetRole | None = None

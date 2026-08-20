"""PromotionTarget entity — who a promotion applies to."""

import re
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, StringConstraints

from src.core.entities.base import BaseEntity
from src.core.enums.promotion_enums import TargetKind

# Roles a target can play in rule-side targeting. `None` (default) keeps the
# legacy semantics: a global eligibility filter checked by
# PromotionEligibilityChecker. Non-null roles are read by the discount
# calculator when filtering cart lines — they don't gate eligibility, only
# restrict which lines participate in the rule.
#
#   • "buy_set"  — BOGO "customer buys X"; MULTIBUY's eligible pool.
#   • "get_set"  — BOGO "customer gets Y".
#   • "leg:<i>"  — BUNDLE: the catalogue scope of leg `i`, positionally
#                  aligned with `DiscountRule.bundle_legs`.
#
# A PATTERN rather than a Literal because the leg space is open-ended, and a
# pattern rather than a bare `str` because `promotion_targets.role` is where a
# typo would silently turn a line filter into a no-op — a "leg:O" (letter O)
# scopes nothing, and a leg that scopes nothing matches EVERY product. The
# 32-char bound is the column's.
LEG_ROLE_PREFIX = "leg:"
TARGET_ROLE_PATTERN = r"^(buy_set|get_set|leg:(0|[1-9][0-9]?))$"
TargetRole = Annotated[
    str, StringConstraints(pattern=TARGET_ROLE_PATTERN, max_length=32)
]

_LEG_ROLE_RE = re.compile(r"^leg:(\d{1,2})$")


def leg_role(index: int) -> str:
    """The role string that scopes BUNDLE leg `index`."""
    return f"{LEG_ROLE_PREFIX}{index}"


def leg_index(role: str | None) -> int | None:
    """`"leg:2"` → 2. None for every other role, including malformed ones."""
    if not role:
        return None
    match = _LEG_ROLE_RE.match(role)
    return int(match.group(1)) if match else None


class PromotionTarget(BaseEntity):
    """Audience / catalog / geo rule attached to a promotion.

    A promotion is eligible iff every `inclusion=True` rule (with
    `role=None`) matches AND no `inclusion=False` rule matches. Empty
    target set = applies to everyone / everything. Role-tagged targets
    (`role="buy_set" | "get_set" | "leg:<i>"`) bypass the eligibility checker
    and feed the discount calculator line filters. The shape of `target_value`
    depends on `target_kind`; see `services.promotion_eligibility_checker`
    and `services.discount_calculator` for the matching logic.
    """

    tenant_id: UUID
    promotion_id: UUID
    target_kind: TargetKind
    target_value: dict[str, Any] = Field(default_factory=dict)
    inclusion: bool = True
    role: TargetRole | None = None

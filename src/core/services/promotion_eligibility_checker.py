"""Eligibility checking for a single promotion against a visitor context."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionStatus, TargetKind


@dataclass(frozen=True)
class EligibilityContext:
    """Everything the checker needs to decide.

    Pure data — never reads from a repo. Callers (the resolver) prefetch
    customer tags, dismissed ids, etc., and pass them in.
    """

    customer_id: UUID | None = None
    customer_tags: list[str] = field(default_factory=list)
    cart_subtotal_cents: int = 0
    cart_product_ids: list[UUID] = field(default_factory=list)
    cart_category_ids: list[UUID] = field(default_factory=list)
    country: str | None = None
    city: str | None = None
    device: str = "desktop"
    is_first_visit: bool = False
    is_logged_in: bool = False
    dismissed_promotion_ids: set[UUID] = field(default_factory=set)


@dataclass(frozen=True)
class EligibilityResult:
    """Decision + a reason list for analytics / debugging."""

    eligible: bool
    reasons: list[str] = field(default_factory=list)


class PromotionEligibilityChecker:
    """Stateless service. Pure function over (promotion, targets, context)."""

    def is_eligible(
        self,
        promotion: Promotion,
        targets: Sequence[PromotionTarget],
        context: EligibilityContext,
        *,
        now: datetime | None = None,
    ) -> EligibilityResult:
        moment = now or datetime.now(UTC)
        reasons: list[str] = []

        # 1. Status & schedule
        if promotion.status != PromotionStatus.ACTIVE:
            return EligibilityResult(False, [f"status={promotion.status}"])
        if promotion.starts_at is not None and moment < promotion.starts_at:
            return EligibilityResult(False, ["scheduled in the future"])
        if promotion.ends_at is not None and moment > promotion.ends_at:
            return EligibilityResult(False, ["window has ended"])

        # 2. Dismissal short-circuit
        if promotion.id in context.dismissed_promotion_ids:
            return EligibilityResult(False, ["dismissed by visitor"])

        # 3. Targets
        for target in targets:
            matches = self._target_matches(target, context)
            if target.inclusion and not matches:
                reasons.append(
                    f"include target {target.target_kind.value} did not match"
                )
                return EligibilityResult(False, reasons)
            if not target.inclusion and matches:
                reasons.append(f"exclude target {target.target_kind.value} matched")
                return EligibilityResult(False, reasons)

        return EligibilityResult(True, [])

    # ------------------------------------------------------------------ #
    # Per-kind matchers                                                   #
    # ------------------------------------------------------------------ #

    def _target_matches(
        self, target: PromotionTarget, context: EligibilityContext
    ) -> bool:
        match target.target_kind:
            case TargetKind.AUDIENCE:
                return self._match_audience(target.target_value, context)
            case TargetKind.PRODUCT:
                ids = {UUID(s) for s in target.target_value.get("product_ids", [])}
                return any(pid in ids for pid in context.cart_product_ids)
            case TargetKind.CATEGORY:
                ids = {UUID(s) for s in target.target_value.get("category_ids", [])}
                return any(cid in ids for cid in context.cart_category_ids)
            case TargetKind.CUSTOMER_TAG:
                tags = set(target.target_value.get("tags", []))
                return any(t in tags for t in context.customer_tags)
            case TargetKind.GEO:
                countries = target.target_value.get("countries") or []
                cities = target.target_value.get("cities") or []
                country_ok = (not countries) or (context.country in countries)
                city_ok = (not cities) or (context.city in cities)
                return country_ok and city_ok
        return False

    def _match_audience(self, value: dict, context: EligibilityContext) -> bool:
        kind = value.get("kind", "all")
        match kind:
            case "all":
                return True
            case "new_visitor":
                return context.is_first_visit
            case "returning":
                return not context.is_first_visit
            case "logged_in":
                return context.is_logged_in
            case "guest":
                return not context.is_logged_in
        return False

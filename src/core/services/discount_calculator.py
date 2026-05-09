"""DiscountCalculator — combines multiple eligible promotions into a total.

Stacking rules (v1):

* At most ONE code-based discount applies per checkout — the one with
  the largest savings wins.
* All eligible automatic discounts stack additively.
* Free shipping is independent — applies if any promotion grants it.
* Total non-shipping discount can never exceed `subtotal_cents`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionSurface, TargetKind
from src.core.value_objects.discount_rule import (
    CartLine,
    DiscountContext,
    DiscountResult,
    DiscountRule,
    DiscountRuleKind,
    LineFilter,
)


@dataclass(frozen=True)
class DiscountedLine:
    """Per-line discount allocation (placeholder for v1 — empty list)."""

    product_id: UUID
    discount_cents: int


@dataclass(frozen=True)
class DiscountTotalResult:
    """Final answer the application/checkout layer applies to the order."""

    line_items: list[DiscountedLine] = field(default_factory=list)
    code_discount_cents: int = 0
    automatic_discount_cents: int = 0
    free_shipping: bool = False
    applied_promotion_ids: list[UUID] = field(default_factory=list)
    rejected: list[tuple[UUID, str]] = field(default_factory=list)

    @property
    def total_discount_cents(self) -> int:
        return self.code_discount_cents + self.automatic_discount_cents


class DiscountCalculator:
    """Stateless. Pure function over inputs."""

    def calculate_total(
        self,
        promotions: Sequence[Promotion],
        applied_coupon_codes: Sequence[str],  # noqa: ARG002 — reserved for v2
        context: DiscountContext,
        *,
        targets_by_promotion: dict[UUID, list[PromotionTarget]] | None = None,
    ) -> DiscountTotalResult:
        """Stack a set of promotions over the cart context.

        `targets_by_promotion` is the optional map of promotion_id →
        PromotionTarget rows. When provided, role-tagged targets
        (`role="buy_set" | "get_set"`) feed BOGO line-set filters so
        Shopify-style "customer buys X / customer gets Y" works. When
        omitted, BOGO falls back to the legacy "any-product, cheapest-
        unit free" semantics — every existing caller stays correct.
        """
        applied_ids: list[UUID] = []
        rejected: list[tuple[UUID, str]] = []
        free_shipping = False

        # Split by surface ----------------------------------------------------
        codes: list[Promotion] = []
        autos: list[Promotion] = []
        for p in promotions:
            if p.discount_rule is None:
                rejected.append((p.id, "no discount_rule"))
                continue
            if p.surface == PromotionSurface.DISCOUNT_CODE:
                codes.append(p)
            elif p.surface == PromotionSurface.AUTOMATIC:
                autos.append(p)
            else:
                rejected.append((p.id, f"surface {p.surface.value} has no math"))

        # Codes — pick the single best ---------------------------------------
        code_discount = 0
        if codes:
            best_promo: Promotion | None = None
            best_result: DiscountResult | None = None
            for p in codes:
                assert p.discount_rule is not None
                buy_f, get_f = _build_bogo_filters(p, targets_by_promotion)
                result = p.discount_rule.calculate(
                    context, buy_filter=buy_f, get_filter=get_f
                )
                if (
                    best_result is None
                    or result.discount_cents > best_result.discount_cents
                ):
                    best_promo = p
                    best_result = result
            if best_promo is not None and best_result is not None:
                if best_result.discount_cents == 0 and not best_result.free_shipping:
                    rejected.append((
                        best_promo.id,
                        best_result.explanation or "no savings",
                    ))
                else:
                    code_discount = best_result.discount_cents
                    applied_ids.append(best_promo.id)
                    if best_result.free_shipping:
                        free_shipping = True
                # The other code promos are rejected — at-most-one rule.
                for other in codes:
                    if other.id != best_promo.id:
                        rejected.append((
                            other.id,
                            "another code-based promo had higher savings",
                        ))

        # Automatic discounts — stack additively, capped at subtotal ----------
        auto_running = 0
        for p in autos:
            assert p.discount_rule is not None
            # Pass remaining-subtotal context so each rule respects the cap.
            remaining_subtotal = max(
                0, context.subtotal_cents - code_discount - auto_running
            )
            sub_context = DiscountContext(
                subtotal_cents=remaining_subtotal,
                line_items=context.line_items,
                shipping_cents=context.shipping_cents,
                customer_id=context.customer_id,
            )
            buy_f, get_f = _build_bogo_filters(p, targets_by_promotion)
            result = p.discount_rule.calculate(
                sub_context, buy_filter=buy_f, get_filter=get_f
            )
            if result.free_shipping:
                free_shipping = True
            if result.discount_cents <= 0 and not result.free_shipping:
                rejected.append((p.id, result.explanation or "no savings"))
                continue
            auto_running += result.discount_cents
            applied_ids.append(p.id)

        # Floor non-shipping discount at the subtotal -------------------------
        non_shipping_total = code_discount + auto_running
        if non_shipping_total > context.subtotal_cents:
            overflow = non_shipping_total - context.subtotal_cents
            # Trim the automatic bucket first — code wins precedence.
            auto_running = max(0, auto_running - overflow)

        return DiscountTotalResult(
            line_items=[],
            code_discount_cents=code_discount,
            automatic_discount_cents=auto_running,
            free_shipping=free_shipping,
            applied_promotion_ids=applied_ids,
            rejected=rejected,
        )

    # ------------------------------------------------------------------ #
    # Convenience for callers that already have a single rule             #
    # ------------------------------------------------------------------ #

    def calculate_one(
        self, rule: DiscountRule, context: DiscountContext
    ) -> DiscountResult:
        """Pure passthrough — useful for previewing a draft rule."""
        if rule.kind == DiscountRuleKind.FREE_SHIPPING:
            return DiscountResult(
                discount_cents=0, free_shipping=True, explanation="free shipping"
            )
        return rule.calculate(context)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_bogo_filters(
    promo: Promotion,
    targets_by_promotion: dict[UUID, list[PromotionTarget]] | None,
) -> tuple[LineFilter | None, LineFilter | None]:
    """Build (buy_filter, get_filter) from this promo's role-tagged targets.

    Returns (None, None) when no map was provided OR the promo has no
    role-tagged targets — preserves the legacy "any-product, cheapest-
    unit free" BOGO semantics. Filters look at `target_kind`:

      • PRODUCT   — `target_value["product_ids"]` against `line.product_id`
      • CATEGORY  — `target_value["category_ids"]` against `line.category_id`

    Other target kinds (audience, customer_tag, geo) are eligibility
    rules, not line filters; if a merchant accidentally tags one with
    a role we just ignore it — failing closed (no discount) would be
    surprising for the merchant who can't see the inconsistency.
    """
    if targets_by_promotion is None:
        return None, None

    targets = targets_by_promotion.get(promo.id, [])
    if not targets:
        return None, None

    buy_pids: set[UUID] = set()
    buy_cids: set[UUID] = set()
    get_pids: set[UUID] = set()
    get_cids: set[UUID] = set()
    has_buy = False
    has_get = False

    for t in targets:
        if t.role == "buy_set":
            has_buy = True
            if t.target_kind == TargetKind.PRODUCT:
                buy_pids.update(UUID(s) for s in t.target_value.get("product_ids", []))
            elif t.target_kind == TargetKind.CATEGORY:
                buy_cids.update(UUID(s) for s in t.target_value.get("category_ids", []))
        elif t.role == "get_set":
            has_get = True
            if t.target_kind == TargetKind.PRODUCT:
                get_pids.update(UUID(s) for s in t.target_value.get("product_ids", []))
            elif t.target_kind == TargetKind.CATEGORY:
                get_cids.update(UUID(s) for s in t.target_value.get("category_ids", []))

    def _make(pids: set[UUID], cids: set[UUID]) -> LineFilter:
        # Match if the line is in EITHER the product allow-list OR the
        # category allow-list. Empty allow-lists never match — a role
        # with no entries is a misconfigured promotion (we'd rather
        # produce zero discount than silently apply BOGO to everything).
        def f(line: CartLine) -> bool:
            if pids and line.product_id in pids:
                return True
            if cids and line.category_id is not None and line.category_id in cids:
                return True
            return False

        return f

    buy_f: LineFilter | None = _make(buy_pids, buy_cids) if has_buy else None
    get_f: LineFilter | None = _make(get_pids, get_cids) if has_get else None
    return buy_f, get_f

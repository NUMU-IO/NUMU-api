"""DiscountRule value object — the math for a single discount.

Pure value object: immutable, no I/O, no service dependencies. Given a
`DiscountContext` it returns a `DiscountResult` saying how many cents to
subtract from the subtotal and whether free shipping applies.

The application layer composes multiple `DiscountResult`s via
`services.discount_calculator.DiscountCalculator`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Used by `_bogo` to decide which cart lines participate in the
# "customer buys" set vs the "customer gets" set when the merchant has
# tagged PromotionTarget rows with `role="buy_set"` / `"get_set"`. The
# filter is a plain predicate over `CartLine` so the rule object stays
# free of repository / target-shape knowledge.
LineFilter = Callable[["CartLine"], bool]

# Ceiling on BUNDLE legs. Not arbitrary: each leg's catalogue scope is carried
# by targets tagged `role="leg:<index>"` in a `String(32)` column, and both the
# resolver's per-leg output and the merchant UI get unreadable long before this.
# Three legs covers the reference's widest bundle (tee + cap + towel).
MAX_BUNDLE_LEGS = 5


class DiscountRuleKind(StrEnum):
    """Kinds of discount math the rule can perform."""

    PERCENTAGE = "percentage"
    FIXED = "fixed"
    FREE_SHIPPING = "free_shipping"
    BOGO = "bogo"
    TIERED = "tiered"
    # "Buy N eligible items for a fixed total price P" — e.g. 3 for EGP 650.
    # Groups repeat: 6 eligible units = two groups. Pure integer math over
    # units, so it never drifts the way a percentage approximation does.
    # Carries one OR MORE tiers ("2 for 968, 3 for 1320") on a single rule —
    # see `multibuy_tiers` and the note on its field.
    MULTIBUY = "multibuy"
    # "One from each of these sets, together, for a fixed total price P" —
    # e.g. 1 tee + 1 cap + 1 towel for EGP 1,556. MULTIBUY cannot express this:
    # its eligible set is ONE pool, so a rule scoped to {tees ∪ caps} with N=2
    # also fires on two tees. Each leg has its own catalogue scope, carried on
    # PromotionTarget rows tagged `role="leg:<index>"`.
    BUNDLE = "bundle"


class DiscountTier(BaseModel):
    """One step of a tiered discount: 'spend X, get Y%'."""

    model_config = ConfigDict(frozen=True)

    threshold_cents: int = Field(ge=0)
    percent: int = Field(ge=0, le=100)


class MultibuyTier(BaseModel):
    """One price break on a multibuy: 'N of them together cost P'."""

    model_config = ConfigDict(frozen=True)

    quantity: int = Field(ge=2)
    price_cents: int = Field(gt=0)


class BundleLeg(BaseModel):
    """One required component of a BUNDLE — "1 tee", "2 caps".

    The leg's CATALOGUE SCOPE is not stored here. It lives on
    `PromotionTarget` rows tagged `role="leg:<index>"`, where the index is this
    leg's position in `DiscountRule.bundle_legs`, and reaches the math as a
    `LineFilter` built by `discount_calculator._build_leg_filters`. Keeping
    scope in targets rather than inlining product ids here is what lets the
    existing target machinery — tenant isolation, the replace-for-promotion
    write path, the resolver's eligible-set reporting — carry bundles with no
    parallel implementation.

    `label` is merchant display copy only ("1 tee"). Nothing computes from it;
    the theme is free to ignore it and render `quantity` itself.
    """

    model_config = ConfigDict(frozen=True)

    quantity: int = Field(default=1, ge=1)
    label: str | None = Field(default=None, max_length=80)


@dataclass(frozen=True)
class CartLine:
    """Slim view of a cart line for discount math."""

    product_id: UUID
    quantity: int
    unit_price_cents: int
    category_id: UUID | None = None


@dataclass(frozen=True)
class DiscountContext:
    """Inputs the calculator needs to evaluate a rule.

    Pure data — never reaches into a repository or session.
    """

    subtotal_cents: int
    line_items: list[CartLine]
    shipping_cents: int = 0
    customer_id: UUID | None = None


@dataclass(frozen=True)
class DiscountResult:
    """Outcome of one rule evaluation."""

    discount_cents: int  # >= 0 — caller floors at subtotal
    free_shipping: bool = False
    affected_line_item_ids: list[UUID] = field(default_factory=list)
    explanation: str = ""


class DiscountRule(BaseModel):
    """Frozen value object — the math for one discount.

    Fields are a superset across all kinds; `_validate_kind_fields`
    enforces which fields each kind requires. Validation never reaches
    the database.
    """

    model_config = ConfigDict(frozen=True)

    kind: DiscountRuleKind
    value_cents: int | None = Field(default=None, ge=0)
    value_percent: int | None = Field(default=None, ge=0, le=100)
    min_subtotal_cents: int | None = Field(default=None, ge=0)
    max_discount_cents: int | None = Field(default=None, ge=0)
    buy_quantity: int | None = Field(default=None, ge=1)
    get_quantity: int | None = Field(default=None, ge=1)
    get_discount_percent: int | None = Field(default=None, ge=0, le=100)
    tiers: list[DiscountTier] = Field(default_factory=list)
    # MULTIBUY — "any N eligible items for a fixed total of P cents".
    # `ge=2` because N=1 would be a per-unit fixed price, not a bundle;
    # `gt=0` because a free bundle should be modelled as a 100% rule so
    # the merchant sees it labelled as such.
    multibuy_quantity: int | None = Field(default=None, ge=2)
    multibuy_price_cents: int | None = Field(default=None, gt=0)
    # MULTIBUY, multi-tier — "2 for 968 AND 3 for 1320" on ONE rule.
    #
    # Modelling each tier as its own promotion is the obvious thing and it is
    # WRONG: automatic promotions stack additively (see
    # `DiscountCalculator.calculate_total`) and two tiers of the same offer do
    # not share unit allocation, so a cart with three caps triggered both — the
    # 2-for took the top two units and the 3-for took all three. Measured on
    # the reference catalogue: advertised 1,320, charged 1,188, and the
    # merchant absorbed the difference silently on every such order. One rule
    # holding every tier is the only shape where that cannot happen.
    #
    # The legacy scalar pair above stays supported and is folded into this list
    # by `resolved_multibuy_tiers`, so existing rows keep working untouched.
    multibuy_tiers: list[MultibuyTier] = Field(default_factory=list)
    # BUNDLE — the legs, in order. Leg i's catalogue scope is the promotion's
    # targets tagged `role="leg:{i}"`.
    bundle_legs: list[BundleLeg] = Field(default_factory=list)
    bundle_price_cents: int | None = Field(default=None, gt=0)

    @property
    def resolved_multibuy_tiers(self) -> list[MultibuyTier]:
        """Every price break this rule offers, LARGEST GROUP FIRST.

        Folds the legacy `multibuy_quantity` / `multibuy_price_cents` pair into
        the tier list so the math has exactly one shape to handle and no call
        site has to know which era a rule was written in. A quantity present in
        both wins from `multibuy_tiers` — the explicit list is the newer, more
        specific statement.

        Descending order is what `_multibuy` walks; it is established here so
        the ordering rule lives with the data rather than in the loop.
        """
        by_quantity: dict[int, int] = {}
        if self.multibuy_quantity is not None and self.multibuy_price_cents is not None:
            by_quantity[self.multibuy_quantity] = self.multibuy_price_cents
        for tier in self.multibuy_tiers:
            by_quantity[tier.quantity] = tier.price_cents
        return [
            MultibuyTier(quantity=q, price_cents=p)
            for q, p in sorted(by_quantity.items(), reverse=True)
        ]

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> Self:
        """Each kind requires a specific subset of fields."""
        match self.kind:
            case DiscountRuleKind.PERCENTAGE:
                if self.value_percent is None:
                    raise ValueError("percentage discount requires value_percent")
            case DiscountRuleKind.FIXED:
                if self.value_cents is None:
                    raise ValueError("fixed discount requires value_cents")
            case DiscountRuleKind.BOGO:
                if self.buy_quantity is None or self.get_quantity is None:
                    raise ValueError(
                        "bogo discount requires buy_quantity and get_quantity"
                    )
                if self.get_discount_percent is None:
                    # default to free
                    object.__setattr__(self, "get_discount_percent", 100)
            case DiscountRuleKind.TIERED:
                if not self.tiers:
                    raise ValueError("tiered discount requires at least one tier")
            case DiscountRuleKind.MULTIBUY:
                # Either shape is complete on its own; a rule may carry both
                # (the scalar pair is then just another tier).
                has_scalar = (
                    self.multibuy_quantity is not None
                    and self.multibuy_price_cents is not None
                )
                if not has_scalar and not self.multibuy_tiers:
                    raise ValueError(
                        "multibuy discount requires multibuy_quantity and "
                        "multibuy_price_cents, or a non-empty multibuy_tiers"
                    )
                # Half a scalar pair is a typo, not a shape. Rejecting it here
                # stops a rule that silently prices at the wrong tier.
                if (self.multibuy_quantity is None) != (
                    self.multibuy_price_cents is None
                ):
                    raise ValueError(
                        "multibuy_quantity and multibuy_price_cents must be "
                        "set together"
                    )
                # The RAW list, not `resolved_multibuy_tiers` — that property
                # is dict-backed and silently drops the duplicate, so checking
                # it can never fail and the merchant keeps a rule with two
                # prices for the same quantity, one of which is unreachable.
                quantities = [t.quantity for t in self.multibuy_tiers]
                if len(quantities) != len(set(quantities)):
                    raise ValueError("multibuy tiers must have distinct quantities")
            case DiscountRuleKind.BUNDLE:
                if not self.bundle_legs or self.bundle_price_cents is None:
                    raise ValueError(
                        "bundle discount requires bundle_legs and bundle_price_cents"
                    )
                if len(self.bundle_legs) < 2:
                    raise ValueError(
                        "bundle discount needs at least 2 legs — a single leg "
                        "is a multibuy"
                    )
                # The cap is the `leg:<index>` role space: PromotionTarget.role
                # is a 32-char column and the resolver reports legs by index.
                if len(self.bundle_legs) > MAX_BUNDLE_LEGS:
                    raise ValueError(
                        f"bundle discount supports at most {MAX_BUNDLE_LEGS} legs"
                    )
            case DiscountRuleKind.FREE_SHIPPING:
                pass
        return self

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def calculate(
        self,
        context: DiscountContext,
        *,
        buy_filter: LineFilter | None = None,
        get_filter: LineFilter | None = None,
        leg_filters: list[LineFilter | None] | None = None,
    ) -> DiscountResult:
        """Compute the discount for the given context.

        Never returns a negative `discount_cents`; capping below zero is
        handled here so callers can sum results without worrying about
        signs. The `max_discount_cents` cap is also applied.

        `buy_filter` / `get_filter` restrict which cart lines
        participate:

        * BOGO — the "customer buys" / "customer gets" sets. When
          omitted, BOGO falls back to the original "any-product,
          cheapest-unit free" semantics so existing rules without
          role-tagged targets keep their behavior.
        * MULTIBUY — `buy_filter` alone is the *eligible set* (which
          products/categories can form a group). `get_filter` is
          ignored: a multibuy group has no separate give-away side.
          Omitted ⇒ the whole cart is eligible.

        `leg_filters` is BUNDLE's equivalent: one filter per entry in
        `bundle_legs`, positionally aligned, built from the promotion's
        `role="leg:<index>"` targets. A `None` entry (or a short list) means
        that leg matches anything, mirroring how an unscoped multibuy behaves.

        Filters are ignored by every kind that does not name them above.
        """
        if self._below_minimum(context):
            return DiscountResult(
                discount_cents=0,
                explanation=(
                    f"subtotal below minimum ({self.min_subtotal_cents} cents)"
                ),
            )

        match self.kind:
            case DiscountRuleKind.FREE_SHIPPING:
                return DiscountResult(
                    discount_cents=0,
                    free_shipping=True,
                    explanation="free shipping",
                )
            case DiscountRuleKind.PERCENTAGE:
                return self._percentage(context)
            case DiscountRuleKind.FIXED:
                return self._fixed(context)
            case DiscountRuleKind.BOGO:
                return self._bogo(context, buy_filter, get_filter)
            case DiscountRuleKind.TIERED:
                return self._tiered(context)
            case DiscountRuleKind.MULTIBUY:
                return self._multibuy(context, buy_filter)
            case DiscountRuleKind.BUNDLE:
                return self._bundle(context, leg_filters)

        return DiscountResult(discount_cents=0, explanation="unknown rule kind")

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _below_minimum(self, context: DiscountContext) -> bool:
        if self.min_subtotal_cents is None:
            return False
        return context.subtotal_cents < self.min_subtotal_cents

    def _cap(self, raw: int, context: DiscountContext) -> int:
        """Apply max_discount_cents and floor at the subtotal."""
        capped = max(0, raw)
        if self.max_discount_cents is not None:
            capped = min(capped, self.max_discount_cents)
        return min(capped, context.subtotal_cents)

    def _percentage(self, context: DiscountContext) -> DiscountResult:
        assert self.value_percent is not None  # validated
        raw = (context.subtotal_cents * self.value_percent) // 100
        capped = self._cap(raw, context)
        explanation = f"{self.value_percent}% off"
        if self.max_discount_cents is not None and capped == self.max_discount_cents:
            explanation += f" (capped at {self.max_discount_cents} cents)"
        return DiscountResult(discount_cents=capped, explanation=explanation)

    def _fixed(self, context: DiscountContext) -> DiscountResult:
        assert self.value_cents is not None  # validated
        capped = self._cap(self.value_cents, context)
        return DiscountResult(
            discount_cents=capped,
            explanation=f"{self.value_cents} cents off",
        )

    def _bogo(
        self,
        context: DiscountContext,
        buy_filter: LineFilter | None,
        get_filter: LineFilter | None,
    ) -> DiscountResult:
        assert self.buy_quantity is not None and self.get_quantity is not None
        assert self.get_discount_percent is not None

        # Sort once, cheapest first. Both buy-side and get-side scans
        # walk this ordering — the cheapest qualifying unit gets the
        # discount, matching the standard BOGO behavior.
        sorted_lines = sorted(context.line_items, key=lambda li: li.unit_price_cents)

        # Build the buy-side and get-side line lists. When filters are
        # absent (the legacy / no-targeting case) both sets are the
        # whole cart — every line counts toward both the buy threshold
        # and the get-side discount, which keeps the existing
        # "any-product, cheapest-unit free" semantics intact.
        buy_lines = (
            [li for li in sorted_lines if buy_filter(li)]
            if buy_filter is not None
            else sorted_lines
        )
        get_lines = (
            [li for li in sorted_lines if get_filter(li)]
            if get_filter is not None
            else sorted_lines
        )

        # Bundle math runs on whichever side defines the threshold.
        # The buy side decides how many bundles the cart unlocks; the
        # get side caps how many discounted units we can hand out
        # (because we never discount past the customer's actual cart).
        buy_units = sum(li.quantity for li in buy_lines)
        get_units = sum(li.quantity for li in get_lines)

        # Disjoint sets (buy ≠ get): bundles = buy_units / buy_qty.
        # Same set (no filters or overlapping): the bundle has to come
        # out of the same pool, so we use the standard total /
        # (buy + get) form. We detect overlap by identity of the input
        # line lists, not value-equality, since the calculator passes
        # in fresh filters per call.
        if buy_filter is None and get_filter is None:
            bundle_size = self.buy_quantity + self.get_quantity
            total_units = sum(li.quantity for li in sorted_lines)
            bundles = total_units // bundle_size
        else:
            # With explicit sets we treat them as disjoint for the
            # threshold check (Shopify's mental model) and clamp the
            # actual discount handed out by the get-side stock.
            bundles_from_buy = buy_units // self.buy_quantity
            bundles_from_get = get_units // self.get_quantity
            bundles = min(bundles_from_buy, bundles_from_get)

        if bundles == 0:
            return DiscountResult(discount_cents=0, explanation="bogo not met")

        # Discount is applied to up to (bundles × get_quantity) cheapest
        # units from the get-side pool.
        discounted_units = bundles * self.get_quantity
        discount_total = 0
        affected: list[UUID] = []
        remaining = discounted_units
        for line in get_lines:
            if remaining <= 0:
                break
            take = min(line.quantity, remaining)
            unit_off = (line.unit_price_cents * self.get_discount_percent) // 100
            discount_total += unit_off * take
            affected.append(line.product_id)
            remaining -= take

        capped = self._cap(discount_total, context)
        scope = (
            "scoped"
            if (buy_filter is not None or get_filter is not None)
            else "any-product"
        )
        return DiscountResult(
            discount_cents=capped,
            affected_line_item_ids=affected,
            explanation=(
                f"buy {self.buy_quantity} get {self.get_quantity} "
                f"@ {self.get_discount_percent}% off — {bundles} bundle(s) ({scope})"
            ),
        )

    def _multibuy(
        self,
        context: DiscountContext,
        line_filter: LineFilter | None,
    ) -> DiscountResult:
        """Any N eligible items for a fixed total P — e.g. 3 for EGP 650.

        The math is deliberately unit-based rather than line-based: a
        single line with quantity 3 is a valid trio ("Mix & Match"
        allows repeats), so lines are expanded to individual units
        first.

        Units are grouped **most expensive first**, which is the
        customer-optimal grouping: it maximises the saving and avoids
        the "why did the deal use my cheapest items?" support ticket.
        Because the ordering is descending, the first group whose units
        already total <= P proves every later group does too — so we
        stop there rather than ever charging more than regular price.
        """
        tiers = self.resolved_multibuy_tiers
        assert tiers  # validated

        units = _expand_units(context.line_items, line_filter)
        scope = "scoped" if line_filter is not None else "any-product"
        smallest = min(t.quantity for t in tiers)
        if len(units) < smallest:
            return DiscountResult(
                discount_cents=0,
                explanation=(
                    f"multibuy needs {smallest} eligible items, "
                    f"cart has {len(units)} ({scope})"
                ),
            )

        # Tier selection, one group at a time — greedy, not exhaustive.
        #
        # Exact optimisation is a bin-covering problem and would need a DP over
        # unit counts, whose allocation is far harder to explain to a merchant
        # asking why the deal picked those items. Greedy is provably optimal for
        # the only shape a sane tier ladder has (bigger group ⇒ better per-unit
        # price, which is why anyone writes a second tier), the tie-break below
        # covers the flat case, and the guard against ever charging above list
        # holds no matter what a merchant types.
        #
        # With a single tier this reduces exactly to the previous behaviour:
        # take the N most expensive remaining units and stop at the first group
        # that would not save anything. With several tiers, the one chosen at
        # each step is the tier with the best saving PER UNIT CONSUMED, not the
        # biggest total saving. The difference decides who gets the better deal
        # when a small tier is unusually generous, and per-unit is the version
        # that cannot be gamed by dropping one cheap extra item in the bag.
        #
        # Every step consumes at least two units, so this terminates.
        discount_total = 0
        groups_applied = 0
        used_quantities: list[int] = []
        affected: list[UUID] = []
        seen: set[UUID] = set()
        cursor = 0
        while True:
            remaining = len(units) - cursor
            best: tuple[tuple[float, int], int, int] | None = None
            for tier in tiers:
                if tier.quantity > remaining:
                    continue
                chunk_sum = sum(
                    u.price_cents for u in units[cursor : cursor + tier.quantity]
                )
                saving = chunk_sum - tier.price_cents
                if saving <= 0:
                    continue
                # Rank by saving per unit, then by SMALLER group.
                #
                # The tie-break is not cosmetic. Four units and tiers "2 for
                # 40,000 / 3 for 60,000" over 25,000 items tie at 5,000 a unit;
                # taking the 3 strands the fourth unit at list price for 15,000
                # off, while taking the 2 twice saves 20,000. A smaller group
                # leaves more units in play and can never leave fewer, so on a
                # tie it is always at least as good.
                key = (saving / tier.quantity, -tier.quantity)
                if best is None or key > best[0]:
                    best = (key, saving, tier.quantity)
            if best is None:
                # Nothing left that saves money. Units are sorted descending,
                # so no later arrangement of the remainder would either.
                break
            _, saving, quantity = best
            discount_total += saving
            groups_applied += 1
            used_quantities.append(quantity)
            for u in units[cursor : cursor + quantity]:
                if u.product_id not in seen:
                    seen.add(u.product_id)
                    affected.append(u.product_id)
            cursor += quantity

        price_of = {t.quantity: t.price_cents for t in tiers}
        if discount_total <= 0:
            offered = ", ".join(f"{q} for {c}" for q, c in sorted(price_of.items()))
            return DiscountResult(
                discount_cents=0,
                explanation=(
                    f"multibuy price ({offered} cents) is not below the "
                    f"regular price of those items ({scope})"
                ),
            )

        capped = self._cap(discount_total, context)
        shape = ", ".join(
            f"{q} for {price_of[q]} cents" for q in dict.fromkeys(used_quantities)
        )
        return DiscountResult(
            discount_cents=capped,
            affected_line_item_ids=affected,
            explanation=f"{shape} — {groups_applied} group(s) ({scope})",
        )

    def _bundle(
        self,
        context: DiscountContext,
        leg_filters: list[LineFilter | None] | None,
    ) -> DiscountResult:
        """One item from each leg, together, for a fixed total.

        The reference offer this exists for is "1 tee + 1 cap + 1 towel for
        EGP 1,556" — see the note on `DiscountRuleKind.BUNDLE` for why a
        multibuy over the union of those three collections is a DIFFERENT
        offer, one that quietly charges the bundle price for three tees.

        Allocation is greedy and in DECLARED LEG ORDER over a pool of units,
        each of which can be spent once. The order only matters when two legs
        overlap (a product that is both a tee and a cap); it is deterministic,
        and it is the order the merchant wrote, which is the only ordering they
        can predict. Within a leg the most expensive available unit is taken
        first — the same customer-optimal rule `_multibuy` uses, for the same
        reason: nobody wants to be told the deal used their cheapest items.

        As in `_multibuy`, a set whose regular total is already at or below the
        bundle price is not discounted. The offer may never make a customer
        worse off, and repeats stop at the first set that would.
        """
        assert self.bundle_price_cents is not None  # validated
        legs = self.bundle_legs
        filters = leg_filters or []

        units = _expand_units(context.line_items, None)
        # Indices into `units`. A unit belongs to at most one leg of at most
        # one bundle.
        consumed: set[int] = set()

        def _take(leg_index: int, quantity: int) -> list[int] | None:
            """Indices for one leg's share, or None when it cannot be filled."""
            leg_filter = filters[leg_index] if leg_index < len(filters) else None
            picked: list[int] = []
            for i, unit in enumerate(units):
                if len(picked) == quantity:
                    break
                if i in consumed:
                    continue
                # A leg with no targets matches anything — the same tolerance
                # `_build_line_filters` applies to an unscoped multibuy.
                if leg_filter is not None and not leg_filter(unit.line):
                    continue
                picked.append(i)
            return picked if len(picked) == quantity else None

        discount_total = 0
        bundles = 0
        affected: list[UUID] = []
        seen: set[UUID] = set()
        while True:
            chosen: list[int] = []
            complete = True
            for leg_index, leg in enumerate(legs):
                take = _take(leg_index, leg.quantity)
                if take is None:
                    complete = False
                    break
                # Reserve immediately, so a later leg cannot re-spend these.
                consumed.update(take)
                chosen.extend(take)
            if not complete:
                # Release the partial attempt rather than leaving units pinned
                # to a bundle that was never formed.
                consumed.difference_update(chosen)
                break
            bundle_sum = sum(units[i].price_cents for i in chosen)
            if bundle_sum <= self.bundle_price_cents:
                consumed.difference_update(chosen)
                break
            discount_total += bundle_sum - self.bundle_price_cents
            bundles += 1
            for i in chosen:
                pid = units[i].product_id
                if pid not in seen:
                    seen.add(pid)
                    affected.append(pid)

        shape = " + ".join(str(leg.quantity) for leg in legs)
        if discount_total <= 0:
            return DiscountResult(
                discount_cents=0,
                explanation=(
                    f"bundle ({shape}) for {self.bundle_price_cents} cents "
                    f"not satisfied by the cart"
                ),
            )

        capped = self._cap(discount_total, context)
        return DiscountResult(
            discount_cents=capped,
            affected_line_item_ids=affected,
            explanation=(
                f"bundle {shape} for {self.bundle_price_cents} cents — "
                f"{bundles} bundle(s)"
            ),
        )

    def _tiered(self, context: DiscountContext) -> DiscountResult:
        # Highest threshold the cart meets wins.
        eligible = [
            t for t in self.tiers if context.subtotal_cents >= t.threshold_cents
        ]
        if not eligible:
            return DiscountResult(
                discount_cents=0,
                explanation="no tier threshold met",
            )
        winning = max(eligible, key=lambda t: t.threshold_cents)
        raw = (context.subtotal_cents * winning.percent) // 100
        capped = self._cap(raw, context)
        return DiscountResult(
            discount_cents=capped,
            explanation=(
                f"{winning.percent}% off (tier ≥ {winning.threshold_cents} cents)"
            ),
        )


@dataclass(frozen=True)
class _Unit:
    """One purchasable unit, exploded out of a cart line.

    Every group-based rule here counts UNITS, not lines: a single line with
    quantity 3 is a valid trio, because "mix and match" allows repeats. The
    original `line` rides along so a per-leg `LineFilter` — which is written
    against `CartLine` — can be applied to a unit without a second filter type.
    """

    price_cents: int
    product_id: UUID
    line: CartLine


def _expand_units(lines: list[CartLine], line_filter: LineFilter | None) -> list[_Unit]:
    """Eligible lines, exploded to units, MOST EXPENSIVE FIRST.

    Descending order is the customer-optimal grouping and it is what lets both
    `_multibuy` and `_bundle` stop early: once the most expensive remaining set
    no longer beats the offer price, no cheaper set will either.
    """
    eligible = (
        [li for li in lines if line_filter(li)] if line_filter is not None else lines
    )
    units = [
        _Unit(price_cents=li.unit_price_cents, product_id=li.product_id, line=li)
        for li in eligible
        for _ in range(li.quantity)
    ]
    units.sort(key=lambda u: u.price_cents, reverse=True)
    return units

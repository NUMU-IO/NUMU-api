"""MULTIBUY at the application boundary — snapshot, plumbing, `role` DTO.

Three things the "3 for EGP 650" offer needs above the pure math
(``tests/unit/core/promotions/test_multibuy_rule.py``):

* the checkout's ``applied_promotions`` snapshot records what each promo
  actually saved, and still reconciles when checkout trims the bucket;
* the offers ``CartItem``s built at ORDER-CREATE carry ``category_id``
  (and ``VisitorContextInput.cart_category_ids`` is populated) — without
  it a category-scoped rule previews a discount at
  ``POST /cart/discounts`` and then charges full price;
* ``role`` survives the create/update DTO round-trip, which is what makes
  a scoped ``buy_set`` reachable from the merchant hub at all.

Reuses the in-memory fakes from this package's conftest.
"""

import inspect
from uuid import uuid4

import pytest

from src.application.dto.promotion import (
    CreatePromotionInput,
    PromotionTargetInput,
    UpdatePromotionInput,
)
from src.application.dto.promotion_resolution import VisitorContextInput
from src.application.use_cases.promotions.calculate_cart_discounts import (
    CalculateCartDiscountsUseCase,
)
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.application.use_cases.promotions.update_promotion import (
    UpdatePromotionUseCase,
)
from src.core.entities.cart import Cart
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import (
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.services.discount_calculator import DiscountCalculator
from src.core.services.promotion_eligibility_checker import (
    PromotionEligibilityChecker,
)
from src.core.value_objects.cart_item import CartItem
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind
from src.core.value_objects.promotion_content import AutomaticContent

N = 3
P = 65_000
UNIT = 25_000


def _multibuy_promo(ids, name: str = "3 for EGP 650") -> Promotion:
    return Promotion(
        id=uuid4(),
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name=name,
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        content=AutomaticContent(),
        discount_rule=DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=N,
            multibuy_price_cents=P,
        ),
        translations={},
    )


def _percentage_promo(ids, percent: int, name: str = "Welcome") -> Promotion:
    return Promotion(
        id=uuid4(),
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        name=name,
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        content=AutomaticContent(),
        discount_rule=DiscountRule(
            kind=DiscountRuleKind.PERCENTAGE, value_percent=percent
        ),
        translations={},
    )


async def _calc(
    *,
    ids,
    promotion_repo,
    target_repo,
    coupon_repo,
    event_repo,
    cart,
    visitor,
    applied_codes=None,
):
    uc = CalculateCartDiscountsUseCase(
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        coupon_repo=coupon_repo,
        eligibility_checker=PromotionEligibilityChecker(),
        calculator=DiscountCalculator(),
        event_repo=event_repo,
    )
    return await uc.execute(
        store_id=ids["store"],
        tenant_id=ids["tenant"],
        cart=cart,
        applied_coupon_codes=applied_codes or [],
        visitor=visitor,
    )


# --------------------------------------------------------------------------- #
# H. _build_applied_promotions — the order snapshot                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_snapshot_uses_the_engine_split_when_supplied(ids, promotion_repo):
    """Each promo gets its OWN amount, not the whole total on the first."""
    from src.api.v1.routes.storefront.checkout import _build_applied_promotions

    trio = _multibuy_promo(ids)
    welcome = _percentage_promo(ids, 10)
    await promotion_repo.create(trio)
    await promotion_repo.create(welcome)

    snapshot = await _build_applied_promotions(
        promotion_repo,
        ids["store"],
        [trio.id, welcome.id],
        15_000,
        {str(trio.id): 10_000, str(welcome.id): 5_000},
    )

    assert [e["amount"] for e in snapshot] == [10_000, 5_000]
    assert [e["title"] for e in snapshot] == ["3 for EGP 650", "Welcome"]
    assert sum(e["amount"] for e in snapshot) == 15_000


@pytest.mark.asyncio
async def test_snapshot_scales_proportionally_when_checkout_trims(ids, promotion_repo):
    """Engine said 10000 + 5000; only 9000 could be applied → 6000 + 3000."""
    from src.api.v1.routes.storefront.checkout import _build_applied_promotions

    trio = _multibuy_promo(ids)
    welcome = _percentage_promo(ids, 10)
    await promotion_repo.create(trio)
    await promotion_repo.create(welcome)

    snapshot = await _build_applied_promotions(
        promotion_repo,
        ids["store"],
        [trio.id, welcome.id],
        9_000,
        {str(trio.id): 10_000, str(welcome.id): 5_000},
    )

    assert [e["amount"] for e in snapshot] == [6_000, 3_000]
    assert sum(e["amount"] for e in snapshot) == 9_000


@pytest.mark.asyncio
async def test_snapshot_loses_no_cents_to_rounding(ids, promotion_repo):
    """Ratios that don't divide evenly still sum EXACTLY to the applied total.

    10000 : 5001 scaled to 9000 floors to 5999 + 3000 = 8999; the missing
    piaster must land on the largest entry, never be dropped.
    """
    from src.api.v1.routes.storefront.checkout import _build_applied_promotions

    trio = _multibuy_promo(ids)
    welcome = _percentage_promo(ids, 10)
    await promotion_repo.create(trio)
    await promotion_repo.create(welcome)

    snapshot = await _build_applied_promotions(
        promotion_repo,
        ids["store"],
        [trio.id, welcome.id],
        9_000,
        {str(trio.id): 10_000, str(welcome.id): 5_001},
    )

    assert sum(e["amount"] for e in snapshot) == 9_000
    assert snapshot[0]["amount"] == 6_000  # largest entry absorbs the remainder
    assert snapshot[1]["amount"] == 3_000


@pytest.mark.asyncio
async def test_snapshot_legacy_path_unchanged_when_map_omitted(ids, promotion_repo):
    """Regression guard for the 3 pre-existing tests' contract."""
    from src.api.v1.routes.storefront.checkout import _build_applied_promotions

    trio = _multibuy_promo(ids)
    welcome = _percentage_promo(ids, 10)
    await promotion_repo.create(trio)
    await promotion_repo.create(welcome)

    snapshot = await _build_applied_promotions(
        promotion_repo, ids["store"], [trio.id, welcome.id], 15_000
    )

    assert [e["amount"] for e in snapshot] == [15_000, 0]


@pytest.mark.asyncio
async def test_snapshot_empty_map_falls_back_to_legacy(ids, promotion_repo):
    """An empty dict is not a split — don't zero every entry."""
    from src.api.v1.routes.storefront.checkout import _build_applied_promotions

    trio = _multibuy_promo(ids)
    await promotion_repo.create(trio)

    snapshot = await _build_applied_promotions(
        promotion_repo, ids["store"], [trio.id], 10_000, {}
    )

    assert snapshot[0]["amount"] == 10_000


# --------------------------------------------------------------------------- #
# The cart/checkout preview: applied_promotions[].amount is per-promotion     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_use_case_reports_per_promotion_amounts(
    ids, promotion_repo, target_repo, coupon_repo, event_repo
):
    trio = _multibuy_promo(ids)
    welcome = _percentage_promo(ids, 10)
    await promotion_repo.create(trio)
    await promotion_repo.create(welcome)

    pid = uuid4()
    cart = Cart(
        session_id="s",
        store_id=ids["store"],
        customer_id=None,
        items=[
            CartItem(product_id=pid, product_name="Tee", quantity=3, unit_price=UNIT)
        ],
    )
    out = await _calc(
        ids=ids,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        coupon_repo=coupon_repo,
        event_repo=event_repo,
        cart=cart,
        visitor=VisitorContextInput(
            cart_subtotal_cents=3 * UNIT, cart_product_ids=[pid]
        ),
    )

    amounts = {e["title"]: e["amount"] for e in out.applied_promotions}
    assert amounts == {"3 for EGP 650": 10_000, "Welcome": 6_500}
    assert sum(amounts.values()) == out.automatic_discount_cents == 16_500


# --------------------------------------------------------------------------- #
# I. Category plumbing — preview must equal what we charge                    #
# --------------------------------------------------------------------------- #


def _scoped_trio(ids, category_id):
    promo = _multibuy_promo(ids)
    target = PromotionTarget(
        tenant_id=ids["tenant"],
        promotion_id=promo.id,
        target_kind=TargetKind.CATEGORY,
        target_value={"category_ids": [str(category_id)]},
        inclusion=True,
        role="buy_set",
    )
    return promo, target


@pytest.mark.asyncio
async def test_scoped_offer_prices_when_cart_items_carry_category_id(
    ids, promotion_repo, target_repo, coupon_repo, event_repo
):
    """The order-create shape (post-fix): CartItem.category_id populated."""
    cat = uuid4()
    promo, target = _scoped_trio(ids, cat)
    await promotion_repo.create(promo)
    await target_repo.replace_for_promotion(promo.id, [target])

    pid = uuid4()
    cart = Cart(
        session_id="checkout",
        store_id=ids["store"],
        customer_id=None,
        items=[
            CartItem(
                product_id=pid,
                product_name="Tee",
                quantity=3,
                unit_price=UNIT,
                category_id=cat,
            )
        ],
    )
    out = await _calc(
        ids=ids,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        coupon_repo=coupon_repo,
        event_repo=event_repo,
        cart=cart,
        visitor=VisitorContextInput(
            cart_subtotal_cents=3 * UNIT,
            cart_product_ids=[pid],
            cart_category_ids=[cat],
        ),
    )
    assert out.automatic_discount_cents == 10_000


@pytest.mark.asyncio
async def test_scoped_offer_silently_charges_full_price_without_category_id(
    ids, promotion_repo, target_repo, coupon_repo, event_repo
):
    """The bug this plumbing fixes, pinned as a characterization test.

    Same cart, same promo — but the line carries no ``category_id`` (the
    pre-fix order-create shape). The buy_set filter matches nothing, so the
    engine returns 0 while ``POST /cart/discounts`` (which DOES send
    ``category_id``) previewed 10000. That divergence is "promised 650,
    charged 750".
    """
    cat = uuid4()
    promo, target = _scoped_trio(ids, cat)
    await promotion_repo.create(promo)
    await target_repo.replace_for_promotion(promo.id, [target])

    pid = uuid4()
    cart = Cart(
        session_id="checkout",
        store_id=ids["store"],
        customer_id=None,
        items=[
            CartItem(product_id=pid, product_name="Tee", quantity=3, unit_price=UNIT)
        ],
    )
    out = await _calc(
        ids=ids,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        coupon_repo=coupon_repo,
        event_repo=event_repo,
        cart=cart,
        visitor=VisitorContextInput(
            cart_subtotal_cents=3 * UNIT,
            cart_product_ids=[pid],
            cart_category_ids=[cat],
        ),
    )
    assert out.automatic_discount_cents == 0


@pytest.mark.asyncio
async def test_category_eligibility_gate_needs_cart_category_ids(
    ids, promotion_repo, target_repo, coupon_repo, event_repo
):
    """The other half of the plumbing: `VisitorContextInput.cart_category_ids`.

    A `role=None` CATEGORY target is an eligibility gate. With the ids
    populated the promo runs; with them missing the promo is filtered out
    before any math happens.
    """
    cat = uuid4()
    promo = _multibuy_promo(ids)
    gate = PromotionTarget(
        tenant_id=ids["tenant"],
        promotion_id=promo.id,
        target_kind=TargetKind.CATEGORY,
        target_value={"category_ids": [str(cat)]},
        inclusion=True,
        role=None,
    )
    await promotion_repo.create(promo)
    await target_repo.replace_for_promotion(promo.id, [gate])

    pid = uuid4()
    items = [
        CartItem(
            product_id=pid,
            product_name="Tee",
            quantity=3,
            unit_price=UNIT,
            category_id=cat,
        )
    ]
    cart = Cart(
        session_id="checkout", store_id=ids["store"], customer_id=None, items=items
    )

    with_ids = await _calc(
        ids=ids,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        coupon_repo=coupon_repo,
        event_repo=event_repo,
        cart=cart,
        visitor=VisitorContextInput(
            cart_subtotal_cents=3 * UNIT,
            cart_product_ids=[pid],
            cart_category_ids=[cat],
        ),
    )
    without_ids = await _calc(
        ids=ids,
        promotion_repo=promotion_repo,
        target_repo=target_repo,
        coupon_repo=coupon_repo,
        event_repo=event_repo,
        cart=cart,
        visitor=VisitorContextInput(
            cart_subtotal_cents=3 * UNIT, cart_product_ids=[pid]
        ),
    )

    assert with_ids.automatic_discount_cents == 10_000
    assert without_ids.automatic_discount_cents == 0


def test_checkout_route_populates_the_category_map():
    """Source-level lock on the order-create wiring.

    A full route test needs Postgres + the whole checkout dependency graph,
    which this suite deliberately avoids (see the module docstring of
    ``test_offers_at_checkout.py``). The behavioural consequence is covered
    by the two tests above; this pins the three wiring points inside
    ``checkout()`` so a refactor cannot quietly drop them again.
    """
    from src.api.v1.routes.storefront import checkout as checkout_module

    source = inspect.getsource(checkout_module)

    # (1) the map is filled from the product we already load
    assert "product_category_map[item.product_id] = getattr(" in source
    # (2) fed into the offers CartItems
    assert "category_id=product_category_map.get(li.product_id)" in source
    # (3) fed into the eligibility context
    assert "cart_category_ids=[" in source
    assert "product_category_map.get(li.product_id) for li in line_items" in source


# --------------------------------------------------------------------------- #
# `role` DTO plumbing — what makes a scoped buy_set reachable                 #
# --------------------------------------------------------------------------- #


def _create_uc(deps):
    return CreatePromotionUseCase(
        promotion_repo=deps["promotion_repo"],
        display_repo=deps["display_repo"],
        target_repo=deps["target_repo"],
        translation_repo=deps["translation_repo"],
        coupon_repo=deps["coupon_repo"],
        store_repo=deps["store_repo"],
        event_bus=deps["event_bus"],
    )


def test_target_input_defaults_to_no_role():
    t = PromotionTargetInput(
        target_kind=TargetKind.CATEGORY, target_value={"category_ids": []}
    )
    assert t.role is None


def test_target_input_rejects_an_unknown_role():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PromotionTargetInput(
            target_kind=TargetKind.CATEGORY,
            target_value={"category_ids": []},
            role="bonus_set",
        )


@pytest.mark.asyncio
async def test_create_promotion_persists_and_returns_role(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    cat = uuid4()
    uc = _create_uc(locals())
    payload = CreatePromotionInput(
        name="3 for EGP 650",
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        content=AutomaticContent(),
        discount_rule=DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=N,
            multibuy_price_cents=P,
        ),
        targets=[
            PromotionTargetInput(
                target_kind=TargetKind.CATEGORY,
                target_value={"category_ids": [str(cat)]},
                role="buy_set",
            )
        ],
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )

    assert out.targets[0].role == "buy_set"
    stored = await target_repo.list_for_promotion(out.id)
    assert stored[0].role == "buy_set"


@pytest.mark.asyncio
async def test_update_promotion_persists_role(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    deps = locals()
    cat = uuid4()
    created = await _create_uc(deps).execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=CreatePromotionInput(
            name="3 for EGP 650",
            surface=PromotionSurface.AUTOMATIC,
            status=PromotionStatus.ACTIVE,
            content=AutomaticContent(),
            discount_rule=DiscountRule(
                kind=DiscountRuleKind.MULTIBUY,
                multibuy_quantity=N,
                multibuy_price_cents=P,
            ),
        ),
    )
    assert created.targets == []

    update_uc = UpdatePromotionUseCase(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        event_bus=event_bus,
    )
    out = await update_uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        promotion_id=created.id,
        actor_user_id=ids["user"],
        payload=UpdatePromotionInput(
            version=created.version,
            targets=[
                PromotionTargetInput(
                    target_kind=TargetKind.CATEGORY,
                    target_value={"category_ids": [str(cat)]},
                    role="buy_set",
                )
            ],
        ),
    )

    assert out.targets[0].role == "buy_set"
    stored = await target_repo.list_for_promotion(created.id)
    assert stored[0].role == "buy_set"

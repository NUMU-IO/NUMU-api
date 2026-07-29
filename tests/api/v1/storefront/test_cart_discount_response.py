"""WS2 — the cart read must SHOW the discount the checkout will charge.

Before this, `GET /storefront/me/cart` had no discount fields at all: a
shopper whose cart qualified for an automatic promotion saw full price
here and the discounted total one step later at checkout. For an AOV
mechanic ("add a 3rd and pay 650") the cart display *is* the feature —
an offer the shopper never sees fire cannot motivate the extra unit.

These tests drive `_build_cart_response` / `_compute_cart_discounts`
directly with in-memory fakes, the same way `tests/integration/
test_query_counts.py` does for the N+1 budget — no Postgres, no Redis,
no FastAPI app. The promotions engine is reached through the four
concrete repository classes the helper imports *at call time*, so
patching those module attributes is a clean seam.

Coverage, in test-design order:
  A. happy path      — an automatic multibuy prices the cart
  B. boundaries      — 2 units (below N) vs 3 units (at N)
  C. scoping         — a CATEGORY buy_set proves `category_id` really is
                       attached from the loaded products
  D. error path      — a promotions outage must NOT break the cart (P0)
  E. invariants      — reconciliation, non-negative total, and the cost
                       of an empty cart (zero promotion queries)
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.api.v1.routes.storefront.cart import _build_cart_response
from src.core.entities.cart import Cart
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import (
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.value_objects.cart_item import CartItem
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind
from src.core.value_objects.money import Currency, Money
from src.core.value_objects.promotion_content import AutomaticContent

# The vionne "3 for EGP 650" offer, in the engine's units (cents).
N = 3
P = 65_000
UNIT = 25_000

TENANT_ID = uuid4()
STORE_ID = uuid4()


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


class _Recorder:
    """Every promotion-engine touch, in order.

    Instantiating a repository is recorded too: the empty-cart assertion
    is that the engine is never even *constructed*, not merely that it
    ran no queries.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def hit(self, name: str) -> None:
        self.calls.append(name)


class FakeProductRepo:
    """Stand-in for `ProductRepository` — bulk fetch + a `session` handle.

    `_compute_cart_discounts` reaches the DB session through
    `product_repo.session`; the value is opaque here because every
    repository built from it is a fake.
    """

    def __init__(self, products: list[Product]) -> None:
        self._by_id: dict[UUID, Product] = {p.id: p for p in products}
        self.session = object()

    async def get_by_ids(self, entity_ids: list[UUID]) -> list[Product]:
        return [self._by_id[i] for i in entity_ids if i in self._by_id]


def _fake_promotion_repo(recorder: _Recorder, promotions: list[Promotion]):
    class _Repo:
        def __init__(self, session: Any) -> None:  # noqa: ARG002
            recorder.hit("PromotionRepository.__init__")

        async def list_active_for_storefront(
            self, store_id: UUID, now: Any, *, include_drafts: bool = False
        ) -> list[Promotion]:
            recorder.hit("list_active_for_storefront")
            return [p for p in promotions if p.store_id == store_id]

    return _Repo


def _fake_target_repo(recorder: _Recorder, targets: dict[UUID, list[PromotionTarget]]):
    class _Repo:
        def __init__(self, session: Any) -> None:  # noqa: ARG002
            recorder.hit("PromotionTargetRepository.__init__")

        async def list_for_promotion(self, promotion_id: UUID) -> list[PromotionTarget]:
            recorder.hit("list_for_promotion")
            return list(targets.get(promotion_id, []))

    return _Repo


def _fake_coupon_repo(recorder: _Recorder):
    class _Repo:
        def __init__(self, session: Any) -> None:  # noqa: ARG002
            recorder.hit("CouponRepository.__init__")

        async def get_by_id(self, coupon_id: UUID) -> None:
            recorder.hit("coupon.get_by_id")
            return None

    return _Repo


def _fake_event_repo(recorder: _Recorder):
    class _Repo:
        def __init__(self, session: Any) -> None:  # noqa: ARG002
            recorder.hit("PromotionEventRepository.__init__")

    return _Repo


class _ExplodingPromotionRepo:
    """A promotions backend that is down."""

    def __init__(self, session: Any) -> None:  # noqa: ARG002
        pass

    async def list_active_for_storefront(self, *a: Any, **k: Any) -> list[Promotion]:
        raise RuntimeError("promotion store unreachable")


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #


def _run(coro: Any) -> Any:
    """Drive one coroutine on a private loop (see test_query_counts.py)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _product(
    name: str = "Scarf",
    *,
    cents: int = UNIT,
    category_id: UUID | None = None,
    status: ProductStatus = ProductStatus.ACTIVE,
) -> Product:
    return Product(
        id=uuid4(),
        store_id=STORE_ID,
        tenant_id=TENANT_ID,
        name=name,
        slug=name.lower(),
        sku=f"SKU-{name}",
        product_type=ProductType.PHYSICAL,
        status=status,
        price=Money.from_cents(cents, Currency.EGP),
        quantity=50,
        low_stock_threshold=2,
        images=[f"https://example.test/{name.lower()}.jpg"],
        category_id=category_id,
        tags=[],
    )


def _cart(*lines: tuple[Product, int]) -> Cart:
    return Cart(
        id=uuid4(),
        session_id=str(uuid4()),
        store_id=STORE_ID,
        customer_id=uuid4(),
        items=[
            CartItem(
                product_id=p.id,
                product_name=p.name,
                quantity=qty,
                unit_price=p.price.cents,
            )
            for p, qty in lines
        ],
        currency="EGP",
    )


def _multibuy_promo(name: str = "3 for EGP 650") -> Promotion:
    return Promotion(
        id=uuid4(),
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
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


def _category_buy_set(promo: Promotion, category_id: UUID) -> PromotionTarget:
    return PromotionTarget(
        tenant_id=TENANT_ID,
        promotion_id=promo.id,
        target_kind=TargetKind.CATEGORY,
        target_value={"category_ids": [str(category_id)]},
        inclusion=True,
        role="buy_set",
    )


@pytest.fixture
def engine(monkeypatch):
    """Install in-memory promotion repositories behind the cart helper.

    `_compute_cart_discounts` imports the four concrete repository
    classes inside the function body, so patching the module attribute
    takes effect on the next call — no import-order games.
    """

    def _install(
        *,
        promotions: list[Promotion] | None = None,
        targets: dict[UUID, list[PromotionTarget]] | None = None,
        promotion_repo_cls: Any = None,
    ) -> _Recorder:
        recorder = _Recorder()
        import src.infrastructure.repositories.coupon_repository as coupon_mod
        import src.infrastructure.repositories.promotion_event_repository as ev_mod
        import src.infrastructure.repositories.promotion_repository as promo_mod

        monkeypatch.setattr(
            promo_mod,
            "PromotionRepository",
            promotion_repo_cls or _fake_promotion_repo(recorder, promotions or []),
        )
        monkeypatch.setattr(
            promo_mod,
            "PromotionTargetRepository",
            _fake_target_repo(recorder, targets or {}),
        )
        monkeypatch.setattr(coupon_mod, "CouponRepository", _fake_coupon_repo(recorder))
        monkeypatch.setattr(
            ev_mod, "PromotionEventRepository", _fake_event_repo(recorder)
        )
        return recorder

    return _install


# --------------------------------------------------------------------------- #
# A. Happy path                                                               #
# --------------------------------------------------------------------------- #


def test_qualifying_cart_shows_the_discount_the_checkout_will_charge(engine):
    """3 eligible units at 250 → subtotal 750, saving 100, total 650."""
    promo = _multibuy_promo()
    engine(promotions=[promo])

    scarf = _product("Scarf")
    repo = FakeProductRepo([scarf])
    resp = _run(_build_cart_response(_cart((scarf, 3)), repo))
    cart = resp.data

    assert cart.subtotal == 3 * UNIT == 75_000
    assert cart.automatic_discount_cents == 10_000
    assert cart.discount_amount == 10_000
    assert cart.total == P == 65_000
    assert len(cart.applied_promotions) == 1
    entry = cart.applied_promotions[0]
    assert entry.amount == 10_000
    assert entry.title == "3 for EGP 650"  # the promo's real name, not a stub
    assert entry.id == str(promo.id)


def test_applied_promotions_shape_matches_the_order_snapshot(engine):
    """`{id, title, title_ar?, amount}` — one theme component for all three."""
    engine(promotions=[_multibuy_promo()])
    scarf = _product("Scarf")
    resp = _run(_build_cart_response(_cart((scarf, 3)), FakeProductRepo([scarf])))

    entry = resp.data.applied_promotions[0].model_dump()
    assert set(entry) == {"id", "title", "title_ar", "amount"}
    assert isinstance(entry["id"], str)
    assert isinstance(entry["amount"], int)


# --------------------------------------------------------------------------- #
# B. Boundary — the offer must not leak in before it qualifies                #
# --------------------------------------------------------------------------- #


def test_two_units_is_below_the_boundary_and_gets_nothing(engine):
    engine(promotions=[_multibuy_promo()])
    scarf = _product("Scarf")
    resp = _run(_build_cart_response(_cart((scarf, 2)), FakeProductRepo([scarf])))
    cart = resp.data

    assert cart.subtotal == 2 * UNIT
    assert cart.automatic_discount_cents == 0
    assert cart.discount_amount == 0
    assert cart.applied_promotions == []
    assert cart.total == cart.subtotal


def test_four_units_still_gets_exactly_one_group(engine):
    """§4: the 4th unit is full price — one trio, not 1.33 trios."""
    engine(promotions=[_multibuy_promo()])
    scarf = _product("Scarf")
    resp = _run(_build_cart_response(_cart((scarf, 4)), FakeProductRepo([scarf])))
    cart = resp.data

    assert cart.subtotal == 4 * UNIT == 100_000
    assert cart.discount_amount == 10_000
    assert cart.total == 90_000


# --------------------------------------------------------------------------- #
# C. Category scoping — proves `category_id` is really attached               #
# --------------------------------------------------------------------------- #


def test_category_scoped_offer_prices_because_lines_carry_category_id(engine):
    """A `buy_set` CATEGORY target only matches if the line has a category.

    This is the whole reason `_compute_cart_discounts` rebuilds the cart
    from the already-loaded products: the persisted cart rows have no
    category. If the attach ever regresses, the filter matches nothing
    and this drops to 0 while checkout still charges 650.
    """
    category_id = uuid4()
    promo = _multibuy_promo()
    engine(
        promotions=[promo], targets={promo.id: [_category_buy_set(promo, category_id)]}
    )

    scarf = _product("Scarf", category_id=category_id)
    resp = _run(_build_cart_response(_cart((scarf, 3)), FakeProductRepo([scarf])))
    cart = resp.data

    assert cart.automatic_discount_cents == 10_000
    assert cart.total == P
    # …and the line echoes the category back, so the host's
    # `/cart/discounts` preview can send it and agree with this response.
    assert cart.items[0].category_id == str(category_id)


def test_category_scoped_offer_skips_a_line_outside_the_set(engine):
    """Negative half of the same contract — the filter really filters."""
    targeted = uuid4()
    promo = _multibuy_promo()
    engine(promotions=[promo], targets={promo.id: [_category_buy_set(promo, targeted)]})

    other = _product("Sponge", category_id=uuid4())
    resp = _run(_build_cart_response(_cart((other, 3)), FakeProductRepo([other])))
    cart = resp.data

    assert cart.automatic_discount_cents == 0
    assert cart.total == cart.subtotal
    assert cart.items[0].category_id is not None  # exposed either way


def test_category_id_is_null_when_the_product_has_no_category(engine):
    engine(promotions=[])
    loose = _product("Loose", category_id=None)
    resp = _run(_build_cart_response(_cart((loose, 1)), FakeProductRepo([loose])))
    assert resp.data.items[0].category_id is None


# --------------------------------------------------------------------------- #
# D. The cart must never break (P0)                                           #
# --------------------------------------------------------------------------- #


def test_a_promotions_outage_does_not_break_the_cart(engine, caplog):
    """The single most important test here.

    Every storefront page reads the cart. If a promotions failure could
    propagate, one bad promotion row (or a Redis/DB hiccup on the
    promotions tables) takes down the cart on every store at once. The
    contract is: full price, zeroed discount fields, HTTP 200.
    """
    engine(promotion_repo_cls=_ExplodingPromotionRepo)

    scarf = _product("Scarf")
    with caplog.at_level("WARNING", logger="src.api.v1.routes.storefront.cart"):
        resp = _run(_build_cart_response(_cart((scarf, 3)), FakeProductRepo([scarf])))
    cart = resp.data

    assert len(cart.items) == 1  # items still render
    assert cart.items[0].quantity == 3
    assert cart.subtotal == 75_000
    assert cart.automatic_discount_cents == 0
    assert cart.discount_amount == 0
    assert cart.applied_promotions == []
    assert cart.total == cart.subtotal  # full price, never 0 and never missing
    # Proves the failure really happened and was swallowed here, rather
    # than the engine quietly returning 0 — a silent outage is an outage
    # nobody pages for.
    assert "cart_discounts_error" in caplog.text
    assert "promotion store unreachable" in caplog.text


def test_a_malformed_promotion_row_does_not_break_the_cart(engine):
    """Same guarantee for bad *data*, not just a dead backend.

    A promotion whose stored JSONB rule is a kind this build doesn't
    know must degrade to "no discount", not to a 500.
    """
    broken = _multibuy_promo("Rogue")
    object.__setattr__(broken, "discount_rule", None)
    engine(promotions=[broken])

    scarf = _product("Scarf")
    resp = _run(_build_cart_response(_cart((scarf, 3)), FakeProductRepo([scarf])))
    assert resp.data.total == resp.data.subtotal
    assert resp.data.applied_promotions == []


# --------------------------------------------------------------------------- #
# E. Invariants + the cost of the hottest storefront read                     #
# --------------------------------------------------------------------------- #


def test_empty_cart_pays_nothing_for_promotions(engine, caplog):
    """No items ⇒ the engine is never even constructed.

    `GET /cart` is the hottest storefront read (header badge on every
    page) and is overwhelmingly empty. `_compute_cart_discounts` must
    short-circuit BEFORE any repository is built or queried.

    Asserted three ways so the test can't pass vacuously: the helper is
    called directly and must RETURN `(0, 0, [])` (drop the guard and it
    raises instead), no repository is recorded, and — because
    `_build_cart_response` swallows exceptions — nothing is logged to
    the cart's failure channel.
    """
    from src.api.v1.routes.storefront.cart import _compute_cart_discounts

    recorder = engine(promotions=[_multibuy_promo()])

    empty = Cart(
        id=uuid4(),
        session_id=str(uuid4()),
        store_id=STORE_ID,
        customer_id=uuid4(),
        items=[],
        currency="EGP",
    )
    repo = FakeProductRepo([])

    assert _run(_compute_cart_discounts(empty, [], {}, repo)) == (0, 0, [])

    with caplog.at_level("WARNING", logger="src.api.v1.routes.storefront.cart"):
        resp = _run(_build_cart_response(empty, repo))
    cart = resp.data

    assert recorder.calls == [], (
        "an empty cart must not touch the promotions engine; recorded: "
        f"{recorder.calls}"
    )
    assert "cart_discounts_error" not in caplog.text
    assert cart.items == []
    assert cart.subtotal == 0
    assert cart.automatic_discount_cents == 0
    assert cart.discount_amount == 0
    assert cart.total == 0
    assert cart.applied_promotions == []


def test_applied_promotions_reconcile_with_the_automatic_total(engine):
    """Σ applied_promotions[].amount == automatic_discount_cents.

    Two stacked automatics: if the entries didn't carry their own
    amounts the summary would read "Trio — 100 / Welcome — 0" while the
    total said 165. Reconciliation is what makes the breakdown
    trustworthy enough to render.
    """
    trio = _multibuy_promo()
    welcome = Promotion(
        id=uuid4(),
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        name="Welcome 10%",
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        content=AutomaticContent(),
        discount_rule=DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10),
        translations={},
    )
    engine(promotions=[trio, welcome])

    scarf = _product("Scarf")
    resp = _run(_build_cart_response(_cart((scarf, 3)), FakeProductRepo([scarf])))
    cart = resp.data

    assert len(cart.applied_promotions) == 2
    assert (
        sum(e.amount for e in cart.applied_promotions) == cart.automatic_discount_cents
    )
    assert cart.total == cart.subtotal - cart.discount_amount


def test_total_never_goes_negative_when_the_discount_swallows_the_cart(engine):
    """A 100%-off automatic must floor the total at 0, never below.

    The cart response is read by themes that render `total` straight
    into `<Money>`; a negative there reads as a refund the platform
    never promised.
    """
    freebie = Promotion(
        id=uuid4(),
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        name="Everything free",
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        content=AutomaticContent(),
        discount_rule=DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=100),
        translations={},
    )
    engine(promotions=[freebie])

    scarf = _product("Scarf")
    resp = _run(_build_cart_response(_cart((scarf, 3)), FakeProductRepo([scarf])))
    cart = resp.data

    assert cart.discount_amount == cart.subtotal
    assert cart.total == 0


# --------------------------------------------------------------------------- #
# F. Regression D-WS2-2 — price ONLY what the shopper can see                 #
# --------------------------------------------------------------------------- #


def test_an_invisible_line_cannot_complete_a_group(engine):
    """REGRESSION (D-WS2-2). Was: the cart priced the RAW cart.

    `_build_cart_response` drops lines whose product is missing or not
    ACTIVE from `items`/`subtotal`. The engine must drop them too. Before
    the fix, 2 visible units + 1 archived unit unlocked a trio: the cart
    showed 2 items, subtotal 500, "saved 100", pay 400 — while checkout
    (which only ever submits visible lines) found 2 units, applied
    nothing, and charged 500. Cart promises less than checkout charges:
    the "promised 650, charged 750" direction §2.5 warns about.

    The shopper cannot buy the third item, so there is no trio.
    """
    engine(promotions=[_multibuy_promo()])

    live = _product("Live", cents=UNIT)
    archived = _product("Archived", cents=UNIT, status=ProductStatus.ARCHIVED)
    repo = FakeProductRepo([live, archived])
    resp = _run(_build_cart_response(_cart((live, 2), (archived, 1)), repo))
    cart = resp.data

    assert len(cart.items) == 1  # archived line hidden from the shopper
    assert cart.total_quantity == 2  # only 2 purchasable units
    assert cart.subtotal == 2 * UNIT == 50_000
    assert cart.automatic_discount_cents == 0  # …so NO trio
    assert cart.discount_amount == 0
    assert cart.applied_promotions == []
    assert cart.total == cart.subtotal == 50_000


def test_a_deleted_product_line_cannot_complete_a_group(engine):
    """Same rule for the other way a line goes invisible: no product row.

    `products_by_id.get(...)` returns None for a product deleted while it
    sat in a Redis cart. That line is dropped from the response, so it
    must not be priced either.
    """
    engine(promotions=[_multibuy_promo()])

    live = _product("Live", cents=UNIT)
    ghost = _product("Ghost", cents=UNIT)
    # `ghost` is in the cart but NOT in the repo — the deleted-product case.
    repo = FakeProductRepo([live])
    resp = _run(_build_cart_response(_cart((live, 2), (ghost, 1)), repo))
    cart = resp.data

    assert len(cart.items) == 1
    assert cart.total_quantity == 2
    assert cart.automatic_discount_cents == 0
    assert cart.total == cart.subtotal == 50_000


def test_subtotal_minus_discount_equals_total_with_a_hidden_line(engine):
    """REGRESSION (D-WS2-2). The arithmetic a theme renders must close.

    A high-value archived line used to make the computed discount exceed
    the subtotal shown, so `subtotal - discount_amount != total` and a
    theme printing all three showed visibly wrong maths. The visible-only
    pricing set makes the identity hold unconditionally.
    """
    engine(promotions=[_multibuy_promo()])

    live = _product("Live", cents=UNIT)
    archived = _product("Archived", cents=200_000, status=ProductStatus.ARCHIVED)
    repo = FakeProductRepo([live, archived])
    resp = _run(_build_cart_response(_cart((live, 2), (archived, 1)), repo))
    cart = resp.data

    assert cart.subtotal == 50_000
    assert cart.discount_amount <= cart.subtotal
    assert cart.subtotal - cart.discount_amount == cart.total
    assert cart.total >= 0


def test_a_hidden_line_does_not_shrink_a_group_the_visible_lines_earn(engine):
    """The fix must not over-correct: visible units still price normally.

    3 visible units + 1 archived unit ⇒ still exactly one trio off the
    visible three. Without this, "drop the invisible line" could be
    implemented as "bail out entirely" and nobody would notice.
    """
    engine(promotions=[_multibuy_promo()])

    live = _product("Live", cents=UNIT)
    archived = _product("Archived", cents=UNIT, status=ProductStatus.ARCHIVED)
    repo = FakeProductRepo([live, archived])
    resp = _run(_build_cart_response(_cart((live, 3), (archived, 1)), repo))
    cart = resp.data

    assert cart.total_quantity == 3
    assert cart.subtotal == 75_000
    assert cart.automatic_discount_cents == 10_000
    assert cart.total == 65_000
    assert cart.subtotal - cart.discount_amount == cart.total


def test_a_cart_of_only_invisible_lines_pays_nothing_for_promotions(engine, caplog):
    """The short-circuit now keys on the VISIBLE set, not `cart.items`.

    A cart holding nothing but archived lines renders empty, so it must
    also cost nothing — the same guarantee as a literally empty cart.
    Before the fix this cart had `cart.items` non-empty and paid for a
    full promotions load to discount a cart with no visible lines.
    """
    from src.api.v1.routes.storefront.cart import _compute_cart_discounts

    recorder = engine(promotions=[_multibuy_promo()])

    archived = _product("Archived", cents=UNIT, status=ProductStatus.ARCHIVED)
    repo = FakeProductRepo([archived])
    cart_entity = _cart((archived, 3))

    assert _run(_compute_cart_discounts(cart_entity, [], {}, repo)) == (0, 0, [])

    with caplog.at_level("WARNING", logger="src.api.v1.routes.storefront.cart"):
        resp = _run(_build_cart_response(cart_entity, repo))
    cart = resp.data

    assert recorder.calls == [], (
        "a cart with no visible lines must not touch the promotions engine; "
        f"recorded: {recorder.calls}"
    )
    assert "cart_discounts_error" not in caplog.text
    assert cart.items == []
    assert cart.subtotal == 0
    assert cart.automatic_discount_cents == 0
    assert cart.discount_amount == 0
    assert cart.total == 0

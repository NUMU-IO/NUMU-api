"""Step 08 regression tests — N+1 query budgets per hot path.

These tests do NOT need a live database. They drive the hot-path
helpers / services with counting stubs in place of the real
repositories. Each repository method invocation maps 1:1 to a SQL
roundtrip in the real implementation, so a budget on call count is
a budget on query count.

If a future change re-introduces an N+1 — e.g. someone replaces the
``get_by_ids`` call in ``_build_cart_response`` with a per-item
``get_by_id`` loop — the budget assertion blows up with a message
listing every method that was called and how many times.

Lessons from Step 04 applied: sync ``def test_*`` driving a private
asyncio loop. No pytest-asyncio fixtures, no project conftest pool
interaction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from src.api.v1.routes.storefront.cart import _build_cart_response
from src.core.entities.cart import Cart
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.promotion import Promotion
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.services.promotion_eligibility_checker import (
    EligibilityContext,
    PromotionEligibilityChecker,
)
from src.core.services.promotion_resolver import PromotionResolver
from src.core.value_objects.cart_item import CartItem
from src.core.value_objects.money import Currency, Money

# ---------------------------------------------------------------- #
# Counting stubs                                                    #
# ---------------------------------------------------------------- #


class CountingMixin:
    """Records each await of a public ``async def`` method by name."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _bump(self, name: str) -> None:
        self.calls.append(name)


class CountingProductRepo(CountingMixin):
    """Stub for the storefront read paths.

    ``get_by_id`` is the per-row path; ``get_by_ids`` is the bulk
    path the cart builder must use.
    """

    def __init__(self, products: list[Product]) -> None:
        super().__init__()
        self._by_id: dict[UUID, Product] = {p.id: p for p in products}

    async def get_by_id(self, entity_id: UUID) -> Product | None:
        self._bump("get_by_id")
        return self._by_id.get(entity_id)

    async def get_by_ids(self, entity_ids: list[UUID]) -> list[Product]:
        self._bump("get_by_ids")
        return [self._by_id[i] for i in entity_ids if i in self._by_id]


class CountingDisplayRepo(CountingMixin):
    async def list_for_promotion(self, promotion_id: UUID) -> list[Any]:
        self._bump("list_for_promotion")
        return []

    async def list_for_promotions(
        self, promotion_ids: list[UUID]
    ) -> dict[UUID, list[Any]]:
        self._bump("list_for_promotions")
        return {pid: [] for pid in promotion_ids}

    async def replace_for_promotion(self, *a, **k) -> None:
        raise NotImplementedError


class CountingTargetRepo(CountingMixin):
    async def list_for_promotion(self, promotion_id: UUID) -> list[Any]:
        self._bump("list_for_promotion")
        return []

    async def list_for_promotions(
        self, promotion_ids: list[UUID]
    ) -> dict[UUID, list[Any]]:
        self._bump("list_for_promotions")
        return {pid: [] for pid in promotion_ids}

    async def replace_for_promotion(self, *a, **k) -> None:
        raise NotImplementedError


class CountingPromotionRepo(CountingMixin):
    def __init__(self, promotions: list[Promotion]) -> None:
        super().__init__()
        self._promos = list(promotions)

    async def list_active_for_storefront(
        self, store_id: UUID, now: datetime, *, include_drafts: bool = False
    ) -> list[Promotion]:
        self._bump("list_active_for_storefront")
        return [p for p in self._promos if p.store_id == store_id]


class CountingDismissalRepo(CountingMixin):
    async def list_dismissed_promotion_ids(
        self, store_id: UUID, *, customer_id=None, visitor_token=None
    ) -> set[UUID]:
        self._bump("list_dismissed_promotion_ids")
        return set()


# ---------------------------------------------------------------- #
# Helpers                                                           #
# ---------------------------------------------------------------- #


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _product(name: str = "Test") -> Product:
    return Product(
        id=uuid4(),
        store_id=uuid4(),
        tenant_id=uuid4(),
        name=name,
        slug=name.lower(),
        sku=f"SKU-{name}",
        description=None,
        short_description=None,
        product_type=ProductType.PHYSICAL,
        status=ProductStatus.ACTIVE,
        price=Money.from_cents(1000, Currency.EGP),
        quantity=10,
        low_stock_threshold=2,
        images=["https://example.test/a.jpg"],
        tags=[],
    )


def _cart_with_items(store_id: UUID, products: list[Product]) -> Cart:
    items = [
        CartItem(
            product_id=p.id,
            product_name=p.name,
            quantity=1,
            unit_price=p.price.cents,
        )
        for p in products
    ]
    return Cart(
        id=uuid4(),
        session_id=str(uuid4()),
        store_id=store_id,
        customer_id=uuid4(),
        items=items,
        currency="EGP",
    )


def _make_promotion(store_id: UUID, surface: PromotionSurface) -> Promotion:
    from src.core.value_objects.promotion_content import AnnouncementBarContent

    return Promotion(
        id=uuid4(),
        store_id=store_id,
        tenant_id=uuid4(),
        name=f"promo-{uuid4()}",
        surface=surface,
        status=PromotionStatus.ACTIVE,
        content=AnnouncementBarContent(),
        coupon_id=None,
        discount_rule=None,
        priority=0,
        starts_at=None,
        ends_at=None,
        translations={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------- #
# Cart-get N+1 budget                                               #
# ---------------------------------------------------------------- #


def test_cart_build_response_uses_bulk_fetch_regardless_of_item_count() -> None:
    """``_build_cart_response`` must issue a constant number of repo
    calls no matter how many items the cart holds.

    Pre-fix the helper called ``product_repo.get_by_id`` once per
    cart line plus one tail call for the currency lookup. With 5
    items that fanned out to 24 SQL queries in the live audit
    (1 product + 2 selectin relations) × 6 = 24. Post-fix the
    helper bulk-fetches once via ``get_by_ids``.

    Budget here is in terms of *repo calls* (which map 1:1 to the
    primary SELECT in each fetch); the selectin batches for the
    eager-loaded ``store`` / ``category`` relations are additional
    but constant per repo call and not exercised by the stub.
    """
    store_id = uuid4()
    products = [_product(f"P{i}") for i in range(5)]
    cart = _cart_with_items(store_id, products)
    repo = CountingProductRepo(products)

    _run(_build_cart_response(cart, repo))

    by_method: dict[str, int] = {}
    for c in repo.calls:
        by_method[c] = by_method.get(c, 0) + 1

    assert by_method.get("get_by_id", 0) == 0, (
        f"_build_cart_response must NOT call get_by_id per item — "
        f"that re-introduces the cart N+1 fixed in Step 08. "
        f"Recorded calls: {by_method}"
    )
    assert by_method.get("get_by_ids", 0) == 1, (
        f"_build_cart_response should bulk-fetch products with a single "
        f"get_by_ids call. Recorded calls: {by_method}"
    )


def test_cart_build_response_scales_constantly() -> None:
    """Doubling the cart size must NOT double the repo call count."""
    store_id = uuid4()

    repo_small = CountingProductRepo([_product(f"S{i}") for i in range(2)])
    cart_small = _cart_with_items(store_id, list(repo_small._by_id.values()))
    _run(_build_cart_response(cart_small, repo_small))

    repo_big = CountingProductRepo([_product(f"B{i}") for i in range(20)])
    cart_big = _cart_with_items(store_id, list(repo_big._by_id.values()))
    _run(_build_cart_response(cart_big, repo_big))

    assert len(repo_small.calls) == len(repo_big.calls), (
        f"Cart query count grew with item count: small={repo_small.calls} "
        f"big={repo_big.calls}"
    )
    assert len(repo_small.calls) <= 2  # 1 bulk fetch + headroom


# ---------------------------------------------------------------- #
# Promotion resolver N+1 budget                                     #
# ---------------------------------------------------------------- #


def test_promotion_resolver_bulk_fetches_displays_and_targets() -> None:
    """``PromotionResolver.resolve_active_for_visitor`` must call
    ``list_for_promotions`` (bulk) on the display + target repos
    exactly once each, regardless of how many active promotions are
    in scope. Pre-fix it looped ``list_for_promotion`` per promo →
    ``2 * len(promos)`` extra queries."""
    store_id = uuid4()
    promos = [
        _make_promotion(store_id, PromotionSurface.ANNOUNCEMENT_BAR) for _ in range(10)
    ]

    promo_repo = CountingPromotionRepo(promos)
    display_repo = CountingDisplayRepo()
    target_repo = CountingTargetRepo()
    dismissal_repo = CountingDismissalRepo()

    resolver = PromotionResolver(
        promotion_repo=promo_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        dismissal_repo=dismissal_repo,
        eligibility_checker=PromotionEligibilityChecker(),
    )

    ctx = EligibilityContext(
        customer_id=None,
        customer_tags=set(),
        cart_subtotal_cents=0,
        cart_product_ids=set(),
        cart_category_ids=set(),
        country=None,
        city=None,
        device="desktop",
        is_first_visit=True,
        is_logged_in=False,
        dismissed_promotion_ids=set(),
    )

    _run(resolver.resolve_active_for_visitor(store_id, ctx, page_path="/"))

    assert display_repo.calls.count("list_for_promotion") == 0, (
        f"PromotionResolver must NOT call list_for_promotion per promo. "
        f"Recorded display_repo calls: {display_repo.calls}"
    )
    assert target_repo.calls.count("list_for_promotion") == 0, (
        f"PromotionResolver must NOT call list_for_promotion per promo. "
        f"Recorded target_repo calls: {target_repo.calls}"
    )
    assert display_repo.calls.count("list_for_promotions") == 1
    assert target_repo.calls.count("list_for_promotions") == 1


def test_promotion_resolver_call_count_constant_in_promo_count() -> None:
    """Resolving 1 promo vs 50 promos must use the same number of
    repo calls — the bulk-fetch is independent of result-set size."""
    store_id = uuid4()

    def _run_with(n: int) -> tuple[int, int, int, int]:
        promos = [
            _make_promotion(store_id, PromotionSurface.ANNOUNCEMENT_BAR)
            for _ in range(n)
        ]
        promo_repo = CountingPromotionRepo(promos)
        display_repo = CountingDisplayRepo()
        target_repo = CountingTargetRepo()
        dismissal_repo = CountingDismissalRepo()
        resolver = PromotionResolver(
            promotion_repo=promo_repo,
            display_repo=display_repo,
            target_repo=target_repo,
            dismissal_repo=dismissal_repo,
            eligibility_checker=PromotionEligibilityChecker(),
        )
        ctx = EligibilityContext(
            customer_id=None,
            customer_tags=set(),
            cart_subtotal_cents=0,
            cart_product_ids=set(),
            cart_category_ids=set(),
            country=None,
            city=None,
            device="desktop",
            is_first_visit=True,
            is_logged_in=False,
            dismissed_promotion_ids=set(),
        )
        _run(resolver.resolve_active_for_visitor(store_id, ctx, page_path="/"))
        return (
            len(promo_repo.calls),
            len(display_repo.calls),
            len(target_repo.calls),
            len(dismissal_repo.calls),
        )

    one = _run_with(1)
    fifty = _run_with(50)
    assert one == fifty, (
        f"Promotion resolver query count scaled with promo count: "
        f"n=1 → {one}, n=50 → {fifty} (counts are tuples of "
        f"(promo, display, target, dismissal) repo call counts)"
    )

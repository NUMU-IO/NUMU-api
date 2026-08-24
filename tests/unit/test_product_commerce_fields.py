"""Product commerce controls: unlisted, scheduled sale, tax exemption.

These guard money and visibility, so each rule is pinned rather than left
to the callers that happen to read it today.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.application.dto.product import ProductDTO
from src.application.services.tax_resolver import TaxLineInput, TaxResolver
from src.core.entities.product import (
    PURCHASABLE_STATUSES,
    Product,
    ProductStatus,
)
from src.core.value_objects.money import Money

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _product(**overrides) -> Product:
    base = {
        "store_id": uuid4(),
        "name": "Linen scarf",
        "slug": "linen-scarf",
        "price": Money.from_cents(20000, "EGP"),
    }
    base.update(overrides)
    return Product(**base)


class TestScheduledSale:
    def test_no_sale_price_is_never_active(self):
        p = _product(sale_starts_at=NOW - timedelta(days=1))
        assert p.sale_is_active(NOW) is False
        assert p.effective_price(NOW).cents == 20000

    def test_open_window_charges_the_sale_price(self):
        p = _product(
            sale_price=Money.from_cents(14000, "EGP"),
            sale_starts_at=NOW - timedelta(days=1),
            sale_ends_at=NOW + timedelta(days=1),
        )
        assert p.sale_is_active(NOW) is True
        assert p.effective_price(NOW).cents == 14000

    def test_not_started_yet(self):
        p = _product(
            sale_price=Money.from_cents(14000, "EGP"),
            sale_starts_at=NOW + timedelta(hours=1),
        )
        assert p.sale_is_active(NOW) is False
        assert p.effective_price(NOW).cents == 20000

    def test_already_ended(self):
        p = _product(
            sale_price=Money.from_cents(14000, "EGP"),
            sale_ends_at=NOW - timedelta(seconds=1),
        )
        assert p.sale_is_active(NOW) is False
        assert p.effective_price(NOW).cents == 20000

    def test_open_ended_bounds_mean_no_bound(self):
        # Neither end set: a sale that runs until the merchant removes it.
        p = _product(sale_price=Money.from_cents(14000, "EGP"))
        assert p.sale_is_active(NOW) is True
        # Start only: runs indefinitely once open.
        p = _product(
            sale_price=Money.from_cents(14000, "EGP"),
            sale_starts_at=NOW - timedelta(days=30),
        )
        assert p.sale_is_active(NOW) is True

    def test_boundaries_are_inclusive(self):
        p = _product(
            sale_price=Money.from_cents(14000, "EGP"),
            sale_starts_at=NOW,
            sale_ends_at=NOW,
        )
        assert p.sale_is_active(NOW) is True


class TestOnSaleAndDiscountPercent:
    def test_scheduled_sale_lights_the_badge(self):
        # The storefront badge reads `is_on_sale`, which predates scheduled
        # sales and only knew about compare_at_price.
        p = _product(sale_price=Money.from_cents(15000, "EGP"))
        assert p.is_on_sale is True
        assert p.discount_percentage == 25.0

    def test_permanent_markdown_still_works(self):
        p = _product(
            price=Money.from_cents(15000, "EGP"),
            compare_at_price=Money.from_cents(20000, "EGP"),
        )
        assert p.is_on_sale is True
        assert p.discount_percentage == 25.0

    def test_percent_measured_against_compare_at_when_both_present(self):
        p = _product(
            price=Money.from_cents(18000, "EGP"),
            compare_at_price=Money.from_cents(20000, "EGP"),
            sale_price=Money.from_cents(10000, "EGP"),
        )
        assert p.discount_percentage == 50.0

    def test_plain_product_is_not_on_sale(self):
        p = _product()
        assert p.is_on_sale is False
        assert p.discount_percentage == 0.0


class TestPurchasableStatuses:
    def test_unlisted_is_purchasable(self):
        # The point of an unlisted product: someone with the link can buy.
        assert ProductStatus.UNLISTED in PURCHASABLE_STATUSES
        assert ProductStatus.ACTIVE in PURCHASABLE_STATUSES

    @pytest.mark.parametrize(
        "status",
        [ProductStatus.DRAFT, ProductStatus.ARCHIVED, ProductStatus.OUT_OF_STOCK],
    )
    def test_other_statuses_are_not(self, status: ProductStatus):
        assert status not in PURCHASABLE_STATUSES


class TestTaxExemption:
    RATE = {"tax_settings": {"enabled": True, "rate": 0.14}}

    def test_exempt_line_contributes_no_tax(self):
        resolver = TaxResolver()
        both = resolver.resolve(
            store_settings=self.RATE,
            line_items=[
                TaxLineInput(unit_price_cents=10000, quantity=1),
                TaxLineInput(unit_price_cents=10000, quantity=1),
            ],
        )
        one_exempt = resolver.resolve(
            store_settings=self.RATE,
            line_items=[
                TaxLineInput(unit_price_cents=10000, quantity=1),
                TaxLineInput(unit_price_cents=10000, quantity=1, taxable=False),
            ],
        )
        assert one_exempt.included_tax_cents == both.included_tax_cents // 2

    def test_exempt_line_reports_zero_rate(self):
        # Visible on the invoice as an exemption rather than looking like
        # a rounding artefact.
        res = TaxResolver().resolve(
            store_settings=self.RATE,
            line_items=[
                TaxLineInput(unit_price_cents=10000, quantity=1, taxable=False)
            ],
        )
        assert res.breakdown[0].rate == 0.0
        assert res.breakdown[0].tax_cents == 0
        assert res.included_tax_cents == 0

    def test_exemption_does_not_shift_tax_onto_other_lines(self):
        # The discount is allocated across ALL lines first; an exempt line
        # must not push its share of tax onto its neighbours.
        resolver = TaxResolver()
        taxed_alone = resolver.resolve(
            store_settings=self.RATE,
            line_items=[TaxLineInput(unit_price_cents=10000, quantity=1)],
        )
        with_exempt_sibling = resolver.resolve(
            store_settings=self.RATE,
            line_items=[
                TaxLineInput(unit_price_cents=10000, quantity=1),
                TaxLineInput(unit_price_cents=50000, quantity=1, taxable=False),
            ],
        )
        assert (
            with_exempt_sibling.breakdown[0].tax_cents
            == taxed_alone.breakdown[0].tax_cents
        )

    def test_lines_default_to_taxable(self):
        line = TaxLineInput(unit_price_cents=100, quantity=1)
        assert line.taxable is True


class TestProductDTOExposure:
    def test_dto_carries_effective_price_separately_from_list_price(self):
        p = _product(
            sale_price=Money.from_cents(14000, "EGP"),
            tax_exempt=True,
            requires_shipping=False,
        )
        dto = ProductDTO.from_entity(p)
        # List price stays put so the storefront can strike it through.
        assert dto.price == p.price.amount
        assert dto.effective_price == p.sale_price.amount
        assert dto.sale_is_active is True
        assert dto.tax_exempt is True
        assert dto.requires_shipping is False

    def test_defaults_match_pre_existing_behaviour(self):
        dto = ProductDTO.from_entity(_product())
        assert dto.requires_shipping is True
        assert dto.tax_exempt is False
        assert dto.sale_is_active is False
        assert dto.effective_price == dto.price
        assert dto.related_product_ids == []

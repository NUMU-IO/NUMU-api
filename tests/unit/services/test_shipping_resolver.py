"""Unit tests for `ShippingResolver` — the pure-function rate evaluator.

These tests don't touch the database. A minimal in-memory repository
stub stands in for `IShippingZoneRepository`; the resolver is exercised
for every rate type and every boundary case called out in the design
doc's verification plan (item 2):

    subtotal at threshold ±1, weight at band boundary ±1 g, COD
    requested when zone disallows, governorate not covered, multiple
    active rates returned in sort order.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.application.services.shipping_resolver import ShippingResolver
from src.core.entities.shipping_rate import RateType, ShippingRate
from src.core.entities.shipping_zone import ShippingZone
from src.core.interfaces.repositories.shipping_zone_repository import (
    IShippingZoneRepository,
)

# ─── In-memory repo stub ─────────────────────────────────────────────


class _InMemoryShippingRepo(IShippingZoneRepository):
    """Just enough repo behaviour to exercise the resolver."""

    def __init__(
        self,
        zones: list[ShippingZone],
        rates_by_zone: dict[UUID, list[ShippingRate]],
    ):
        self._zones = {z.id: z for z in zones}
        self._rates_by_zone = rates_by_zone
        # Flat index for get_rate.
        self._rates: dict[UUID, ShippingRate] = {
            r.id: r for rates in rates_by_zone.values() for r in rates
        }

    async def get_zone(self, zone_id: UUID) -> ShippingZone | None:
        return self._zones.get(zone_id)

    async def get_zone_for_governorate(
        self, store_id: UUID, governorate_code: str
    ) -> ShippingZone | None:
        for z in self._zones.values():
            if (
                z.store_id == store_id
                and z.is_active
                and governorate_code in z.governorate_codes
            ):
                return z
        return None

    async def list_rates_by_zone(
        self, zone_id: UUID, include_inactive: bool = False
    ) -> list[ShippingRate]:
        rates = self._rates_by_zone.get(zone_id, [])
        if not include_inactive:
            rates = [r for r in rates if r.is_active]
        rates = sorted(rates, key=lambda r: (r.sort_order, r.label))
        return rates

    async def get_rate(self, rate_id: UUID) -> ShippingRate | None:
        return self._rates.get(rate_id)

    # Unused-in-tests abstract methods.
    async def create_zone(self, zone, governorate_codes):  # pragma: no cover
        raise NotImplementedError

    async def list_zones_by_store(  # pragma: no cover
        self, store_id, include_inactive=False
    ):
        return [
            z
            for z in self._zones.values()
            if z.store_id == store_id and (include_inactive or z.is_active)
        ]

    async def update_zone(self, zone, governorate_codes=None):  # pragma: no cover
        raise NotImplementedError

    async def delete_zone(self, zone_id):  # pragma: no cover
        raise NotImplementedError

    async def hard_delete_zone(self, zone_id):  # pragma: no cover
        raise NotImplementedError

    async def create_rate(self, rate):  # pragma: no cover
        raise NotImplementedError

    async def update_rate(self, rate):  # pragma: no cover
        raise NotImplementedError

    async def delete_rate(self, rate_id):  # pragma: no cover
        raise NotImplementedError

    async def get_zones_with_rates_for_store(self, store_id):  # pragma: no cover
        return [
            (z, await self.list_rates_by_zone(z.id))
            for z in self._zones.values()
            if z.store_id == store_id and z.is_active
        ]

    async def get_covered_governorate_codes(self, store_id):  # pragma: no cover
        covered: set[str] = set()
        for z in self._zones.values():
            if z.store_id == store_id and z.is_active:
                covered.update(z.governorate_codes)
        return covered

    async def has_active_zones(self, store_id):  # pragma: no cover
        return any(z.store_id == store_id and z.is_active for z in self._zones.values())


# ─── Fixture builders ────────────────────────────────────────────────


STORE_ID = UUID("00000000-0000-0000-0000-000000000001")
TENANT_ID = UUID("00000000-0000-0000-0000-000000000002")


def _zone(
    *,
    governorate_codes: list[str],
    cod_enabled: bool = True,
    cod_fee_cents: int = 0,
    is_active: bool = True,
) -> ShippingZone:
    return ShippingZone(
        id=uuid4(),
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        name="Test Zone",
        governorate_codes=governorate_codes,
        cod_enabled=cod_enabled,
        cod_fee_cents=cod_fee_cents,
        is_active=is_active,
        estimated_days_min=1,
        estimated_days_max=3,
    )


def _flat_rate(
    zone_id: UUID, *, amount: int, label: str = "Standard", sort: int = 0
) -> ShippingRate:
    return ShippingRate(
        id=uuid4(),
        tenant_id=TENANT_ID,
        zone_id=zone_id,
        rate_type=RateType.FLAT,
        label=label,
        config={"amount_cents": amount},
        sort_order=sort,
    )


def _free_over_rate(
    zone_id: UUID,
    *,
    amount: int,
    threshold: int,
    label: str = "Standard",
) -> ShippingRate:
    return ShippingRate(
        id=uuid4(),
        tenant_id=TENANT_ID,
        zone_id=zone_id,
        rate_type=RateType.FREE_OVER,
        label=label,
        config={
            "amount_cents": amount,
            "free_when_subtotal_gte_cents": threshold,
        },
    )


def _weight_band_rate(
    zone_id: UUID,
    *,
    bands: list[dict],
    label: str = "Standard",
) -> ShippingRate:
    return ShippingRate(
        id=uuid4(),
        tenant_id=TENANT_ID,
        zone_id=zone_id,
        rate_type=RateType.WEIGHT_BAND,
        label=label,
        config={"bands": bands},
    )


def _resolver_with(zone: ShippingZone, rates: list[ShippingRate]) -> ShippingResolver:
    repo = _InMemoryShippingRepo([zone], {zone.id: rates})
    return ShippingResolver(repo, currency="EGP")


# ─── Flat rate ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flat_rate_returns_configured_amount():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _flat_rate(zone.id, amount=5000)
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=1200,
    )
    assert len(result.options) == 1
    assert result.options[0].amount_cents == 5000
    assert result.options[0].rate_id == rate.id


# ─── Free-over rate (boundary tests) ─────────────────────────────────


@pytest.mark.asyncio
async def test_free_over_charges_below_threshold():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _free_over_rate(zone.id, amount=5000, threshold=50000)
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=49999,
        cart_weight_g=0,
    )
    assert result.options[0].amount_cents == 5000
    assert result.free_shipping_progress is not None
    assert result.free_shipping_progress.remaining_cents == 1
    assert result.free_shipping_progress.qualified is False


@pytest.mark.asyncio
async def test_free_over_is_free_at_threshold():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _free_over_rate(zone.id, amount=5000, threshold=50000)
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=50000,
        cart_weight_g=0,
    )
    assert result.options[0].amount_cents == 0
    assert result.free_shipping_progress.qualified is True
    assert result.free_shipping_progress.remaining_cents == 0


@pytest.mark.asyncio
async def test_free_over_is_free_above_threshold():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _free_over_rate(zone.id, amount=5000, threshold=50000)
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=50001,
        cart_weight_g=0,
    )
    assert result.options[0].amount_cents == 0


# ─── Weight bands (boundary tests) ───────────────────────────────────


@pytest.mark.asyncio
async def test_weight_band_first_band():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _weight_band_rate(
        zone.id,
        bands=[
            {"max_weight_g": 3000, "amount_cents": 5000},
            {"max_weight_g": 5000, "amount_cents": 7000},
            {"max_weight_g": None, "amount_cents": 7000, "per_extra_kg_cents": 1000},
        ],
    )
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=2900,
    )
    assert result.options[0].amount_cents == 5000


@pytest.mark.asyncio
async def test_weight_band_boundary_inclusive_lower():
    """Exactly max_weight_g of a band → still that band (inclusive)."""
    zone = _zone(governorate_codes=["EG-C"])
    rate = _weight_band_rate(
        zone.id,
        bands=[
            {"max_weight_g": 3000, "amount_cents": 5000},
            {"max_weight_g": 5000, "amount_cents": 7000},
        ],
    )
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=3000,
    )
    assert result.options[0].amount_cents == 5000


@pytest.mark.asyncio
async def test_weight_band_boundary_crosses_to_next():
    """3001 g → next band."""
    zone = _zone(governorate_codes=["EG-C"])
    rate = _weight_band_rate(
        zone.id,
        bands=[
            {"max_weight_g": 3000, "amount_cents": 5000},
            {"max_weight_g": 5000, "amount_cents": 7000},
        ],
    )
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=3001,
    )
    assert result.options[0].amount_cents == 7000


@pytest.mark.asyncio
async def test_weight_band_open_ended_with_per_extra_kg():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _weight_band_rate(
        zone.id,
        bands=[
            {"max_weight_g": 3000, "amount_cents": 5000},
            {"max_weight_g": 5000, "amount_cents": 7000},
            {"max_weight_g": None, "amount_cents": 7000, "per_extra_kg_cents": 1000},
        ],
    )
    resolver = _resolver_with(zone, [rate])

    # 5500 g → previous band max 5000, overflow 500g → ceil(0.5)=1 kg → +1000.
    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=5500,
    )
    assert result.options[0].amount_cents == 8000


@pytest.mark.asyncio
async def test_weight_band_open_ended_no_overflow_surcharge_at_exact_prev_band():
    """Weight exactly equal to prev_band_max → no extra kg charge."""
    zone = _zone(governorate_codes=["EG-C"])
    rate = _weight_band_rate(
        zone.id,
        bands=[
            {"max_weight_g": 5000, "amount_cents": 7000},
            {"max_weight_g": None, "amount_cents": 7000, "per_extra_kg_cents": 1000},
        ],
    )
    resolver = _resolver_with(zone, [rate])

    # At 5000 g we hit the first band, not the open-ended one — base 7000.
    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=5000,
    )
    assert result.options[0].amount_cents == 7000


# ─── COD ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cod_disabled_zone_filters_out_when_cod_requested():
    zone = _zone(governorate_codes=["EG-ASN"], cod_enabled=False)
    rate = _flat_rate(zone.id, amount=9000)
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-ASN",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
        cod_requested=True,
    )
    assert result.options == []


@pytest.mark.asyncio
async def test_cod_enabled_zone_returns_option_with_cod_flag():
    zone = _zone(governorate_codes=["EG-C"], cod_enabled=True)
    rate = _flat_rate(zone.id, amount=5000)
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
        cod_requested=True,
    )
    assert result.options[0].cod_supported is True


@pytest.mark.asyncio
async def test_cod_fee_adds_to_amount_when_requested():
    zone = _zone(governorate_codes=["EG-C"], cod_enabled=True, cod_fee_cents=1500)
    rate = _flat_rate(zone.id, amount=5000)
    resolver = _resolver_with(zone, [rate])

    # Without COD request, the fee should NOT apply.
    result_no_cod = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
        cod_requested=False,
    )
    assert result_no_cod.options[0].amount_cents == 5000

    # With COD request, the fee is added.
    result_cod = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
        cod_requested=True,
    )
    assert result_cod.options[0].amount_cents == 6500


# ─── Governorate not covered ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_uncovered_governorate_returns_no_options():
    zone = _zone(governorate_codes=["EG-C"])  # only covers Cairo
    rate = _flat_rate(zone.id, amount=5000)
    resolver = _resolver_with(zone, [rate])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-ASN",  # Aswan is not in the zone
        cart_subtotal_cents=10000,
        cart_weight_g=0,
    )
    assert result.options == []
    assert result.free_shipping_progress is None


# ─── Multiple rates: sort order preserved ────────────────────────────


@pytest.mark.asyncio
async def test_multiple_rates_returned_in_sort_order():
    zone = _zone(governorate_codes=["EG-C"])
    r_express = _flat_rate(zone.id, amount=8000, label="Express", sort=2)
    r_standard = _flat_rate(zone.id, amount=5000, label="Standard", sort=1)
    resolver = _resolver_with(zone, [r_express, r_standard])

    result = await resolver.resolve_options(
        store_id=STORE_ID,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
    )
    assert [o.label for o in result.options] == ["Standard", "Express"]
    assert [o.amount_cents for o in result.options] == [5000, 8000]


# ─── resolve_one (checkout trust-gap fix) ────────────────────────────


@pytest.mark.asyncio
async def test_resolve_one_recomputes_selected_rate():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _flat_rate(zone.id, amount=5000)
    resolver = _resolver_with(zone, [rate])

    option = await resolver.resolve_one(
        store_id=STORE_ID,
        rate_id=rate.id,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
    )
    assert option is not None
    assert option.amount_cents == 5000
    assert option.zone_id == zone.id


@pytest.mark.asyncio
async def test_resolve_one_rejects_rate_for_wrong_governorate():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _flat_rate(zone.id, amount=5000)
    resolver = _resolver_with(zone, [rate])

    option = await resolver.resolve_one(
        store_id=STORE_ID,
        rate_id=rate.id,
        governorate_code="EG-ASN",  # address doesn't match the rate's zone
        cart_subtotal_cents=10000,
        cart_weight_g=0,
    )
    assert option is None


@pytest.mark.asyncio
async def test_resolve_one_rejects_inactive_rate():
    zone = _zone(governorate_codes=["EG-C"])
    rate = _flat_rate(zone.id, amount=5000)
    rate.is_active = False
    resolver = _resolver_with(zone, [rate])

    option = await resolver.resolve_one(
        store_id=STORE_ID,
        rate_id=rate.id,
        governorate_code="EG-C",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
    )
    assert option is None


@pytest.mark.asyncio
async def test_resolve_one_rejects_cod_for_cod_disabled_zone():
    zone = _zone(governorate_codes=["EG-ASN"], cod_enabled=False)
    rate = _flat_rate(zone.id, amount=9000)
    resolver = _resolver_with(zone, [rate])

    option = await resolver.resolve_one(
        store_id=STORE_ID,
        rate_id=rate.id,
        governorate_code="EG-ASN",
        cart_subtotal_cents=10000,
        cart_weight_g=0,
        cod_requested=True,
    )
    assert option is None

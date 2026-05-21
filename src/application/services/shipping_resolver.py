"""Shipping resolver — pure evaluation of a rate given cart + destination.

Kept free of side effects (no DB writes, no network) on purpose. The
resolver takes a store's active zones+rates (produced by the repository)
plus the cart shape, and emits the available options. Identical input
→ identical output, which makes it:

    * easy to unit-test (stub the repository, cover every rate_type
      boundary),
    * easy to cache (Redis key = store_id + destination + subtotal_band
      + weight_band + cod),
    * safe to call twice (once at `/shipping/options`, once at
      `/checkout` to close the trust gap — results must match).

The resolver is the **only** component allowed to price shipping in
MVP. Checkout never trusts a client-supplied `shipping_cost`.
"""

from dataclasses import dataclass
from uuid import UUID

from src.core.entities.shipping_rate import (
    RateConfigCarrierApi,
    RateConfigFlat,
    RateConfigFreeOver,
    RateConfigWeightBand,
    RateType,
    ShippingRate,
    parse_rate_config,
)
from src.core.entities.shipping_zone import ShippingZone
from src.core.interfaces.repositories.shipping_zone_repository import (
    IShippingZoneRepository,
)


@dataclass(frozen=True)
class ResolvedOption:
    """One rate option resolved for a specific cart / destination."""

    rate_id: UUID
    zone_id: UUID
    label: str
    label_ar: str | None
    amount_cents: int
    currency: str
    estimated_days_min: int
    estimated_days_max: int
    cod_supported: bool
    rate_type: RateType


@dataclass(frozen=True)
class FreeShippingProgress:
    """Nudge shown to customers approaching a free_over threshold."""

    current_cents: int
    threshold_cents: int

    @property
    def remaining_cents(self) -> int:
        return max(0, self.threshold_cents - self.current_cents)

    @property
    def qualified(self) -> bool:
        return self.current_cents >= self.threshold_cents


@dataclass(frozen=True)
class ResolverOutput:
    """Returned by `resolve_options`."""

    options: list[ResolvedOption]
    free_shipping_progress: FreeShippingProgress | None


class ShippingResolver:
    """Pure evaluator for a store's shipping rules."""

    def __init__(
        self,
        repository: IShippingZoneRepository,
        currency: str = "EGP",
    ) -> None:
        self.repository = repository
        self.currency = currency

    # ─── Public API ───────────────────────────────────────────────

    async def resolve_options(
        self,
        *,
        store_id: UUID,
        governorate_code: str,
        cart_subtotal_cents: int,
        cart_weight_g: int,
        cod_requested: bool = False,
        location_id: UUID | None = None,
    ) -> ResolverOutput:
        """Return every available rate option for this cart+destination.

        Used by `GET/POST /storefront/.../shipping/options`.

        Phase 8.2 — accepts an optional `location_id` for multi-
        location stores. Today rates are keyed only on destination
        governorate, so this parameter is forward-compat: when we add
        origin-by-origin rate configs (different price ex-Cairo vs.
        ex-Alex), the resolver can pick the right ones without
        breaking callers. Single-location stores leave it None.
        """
        # Touch the param so static analyzers don't flag it unused —
        # the documentation above commits to the surface, and rate
        # evaluation has a hook for it later.
        _ = location_id
        zone = await self.repository.get_zone_for_governorate(
            store_id, governorate_code
        )
        if zone is None:
            return ResolverOutput(options=[], free_shipping_progress=None)

        rates = await self.repository.list_rates_by_zone(zone.id)
        options: list[ResolvedOption] = []
        lowest_free_over_threshold: int | None = None

        for rate in rates:
            # Respect per-zone COD policy when the customer has asked for COD.
            if cod_requested and not zone.cod_enabled:
                continue
            evaluated = self._evaluate_rate(
                rate=rate,
                zone=zone,
                cart_subtotal_cents=cart_subtotal_cents,
                cart_weight_g=cart_weight_g,
                cod_requested=cod_requested,
            )
            if evaluated is None:
                continue
            options.append(evaluated)
            # Track the lowest free_over threshold so the progress nudge
            # shows the closest goal.
            if rate.rate_type == RateType.FREE_OVER:
                cfg = parse_rate_config(rate.rate_type, rate.config)
                assert isinstance(cfg, RateConfigFreeOver)
                if (
                    lowest_free_over_threshold is None
                    or cfg.free_when_subtotal_gte_cents < lowest_free_over_threshold
                ):
                    lowest_free_over_threshold = cfg.free_when_subtotal_gte_cents

        progress: FreeShippingProgress | None = None
        if lowest_free_over_threshold is not None:
            progress = FreeShippingProgress(
                current_cents=max(0, cart_subtotal_cents),
                threshold_cents=lowest_free_over_threshold,
            )
        return ResolverOutput(options=options, free_shipping_progress=progress)

    async def resolve_one(
        self,
        *,
        store_id: UUID,
        rate_id: UUID,
        governorate_code: str,
        cart_subtotal_cents: int,
        cart_weight_g: int,
        cod_requested: bool = False,
        location_id: UUID | None = None,
    ) -> ResolvedOption | None:
        """Re-resolve a specific rate for server-side verification.

        Used by `/checkout` to recompute the price for the rate the
        customer picked. Returns None if the rate is no longer available
        (deactivated, moved to a different zone, or the destination
        governorate changed and is no longer covered by the rate's zone).

        Phase 8.2 — `location_id` accepted for forward-compat; today
        rate evaluation is destination-only.
        """
        _ = location_id
        rate = await self.repository.get_rate(rate_id)
        if rate is None or not rate.is_active:
            return None
        zone = await self.repository.get_zone(rate.zone_id)
        if zone is None or not zone.is_active:
            return None
        # The rate's zone must actually cover the destination governorate.
        if governorate_code not in zone.governorate_codes:
            return None
        # Honour zone COD policy.
        if cod_requested and not zone.cod_enabled:
            return None
        return self._evaluate_rate(
            rate=rate,
            zone=zone,
            cart_subtotal_cents=cart_subtotal_cents,
            cart_weight_g=cart_weight_g,
            cod_requested=cod_requested,
        )

    # ─── Evaluation (pure, testable) ──────────────────────────────

    def _evaluate_rate(
        self,
        *,
        rate: ShippingRate,
        zone: ShippingZone,
        cart_subtotal_cents: int,
        cart_weight_g: int,
        cod_requested: bool,
    ) -> ResolvedOption | None:
        """Return a priced option for the given rate, or None if N/A.

        Branch on rate_type. Each branch is independently unit-tested.
        """
        cfg = parse_rate_config(rate.rate_type, rate.config)
        amount_cents: int

        if isinstance(cfg, RateConfigFlat):
            amount_cents = cfg.amount_cents

        elif isinstance(cfg, RateConfigFreeOver):
            if cart_subtotal_cents >= cfg.free_when_subtotal_gte_cents:
                amount_cents = 0
            else:
                amount_cents = cfg.amount_cents

        elif isinstance(cfg, RateConfigWeightBand):
            amount_cents = self._evaluate_weight_band(cfg, cart_weight_g)

        elif isinstance(cfg, RateConfigCarrierApi):
            # Post-MVP: call IShippingService.get_rates() here.
            # In MVP we simply skip — the rate is schema-present but
            # not wired, so a merchant who creates one won't crash
            # checkout, but the option also won't surface.
            return None
        else:  # pragma: no cover — validator covers unknowns
            return None

        # Apply COD surcharge if customer chose COD and zone charges a fee.
        if cod_requested and zone.cod_enabled and zone.cod_fee_cents > 0:
            amount_cents += zone.cod_fee_cents

        return ResolvedOption(
            rate_id=rate.id,
            zone_id=zone.id,
            label=rate.label,
            label_ar=rate.label_ar,
            amount_cents=amount_cents,
            currency=self.currency,
            estimated_days_min=zone.estimated_days_min,
            estimated_days_max=zone.estimated_days_max,
            cod_supported=zone.cod_enabled,
            rate_type=rate.rate_type,
        )

    @staticmethod
    def _evaluate_weight_band(cfg: RateConfigWeightBand, cart_weight_g: int) -> int:
        """First-match band evaluation with open-ended per-extra-kg.

        Bands are expected to arrive sorted ascending by max_weight_g
        (None last); the API layer sorts on write. We still defend
        against out-of-order data by explicitly iterating in sorted
        order here.
        """
        # Sort: finite max_weight_g first (ascending), open-ended last.
        sorted_bands = sorted(
            cfg.bands,
            key=lambda b: (b.max_weight_g is None, b.max_weight_g or 0),
        )
        prev_band_max: int = 0
        for band in sorted_bands:
            if band.max_weight_g is None:
                # Open-ended band — applies for any remaining weight.
                amount = band.amount_cents
                if band.per_extra_kg_cents:
                    # Charge per started kg above `prev_band_max`.
                    overflow_g = max(0, cart_weight_g - prev_band_max)
                    extra_kg = (overflow_g + 999) // 1000  # ceil
                    amount += extra_kg * band.per_extra_kg_cents
                return amount
            if cart_weight_g <= band.max_weight_g:
                return band.amount_cents
            prev_band_max = band.max_weight_g
        # Shouldn't reach here if the config has at least one band (which
        # RateConfigWeightBand's Field(min_length=1) enforces) — but be
        # defensive and fall back to the last band's amount.
        last = sorted_bands[-1]
        return last.amount_cents

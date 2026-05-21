"""Tax resolver — pure evaluation of order tax given cart + store settings.

Mirrors the architecture of `shipping_resolver.py`: the resolver is a
pure function (no DB writes, no network) that takes line items + the
store's `tax_settings` JSON and emits the tax amount in cents plus
per-line breakdown for invoice display.

Why we don't trust client-supplied tax:
    The pre-checkout cart UI may show a tentative tax total computed by
    the SDK for transparency, but at order-create time the server
    re-resolves from store settings. A tampered request that sets
    `tax_amount=0` must never go through.

v1 scope (MENA-first):
    - default_rate: a single VAT rate applied to the post-discount
      subtotal (Egyptian default: 0.14 / 14%).
    - included_in_price: when true, the merchant's listed prices already
      include VAT — we back-compute the tax-component for invoice
      reporting without changing the order total. When false, VAT is
      ADDED on top.
    - zone_overrides: optional per-governorate overrides keyed by ISO
      3166-2 code (e.g. {"EG-C": 0.0} for a tax-free zone).
    - per_product_class_rates: reserved (Phase 3) — currently ignored;
      every line uses the resolved store/zone rate.

Future-proofing:
    The resolver returns a structured ResolvedTax with both the total
    cents to add to the order and the implicit "included_tax_cents"
    component for receipts / e-invoices. The `tax_amount` field on the
    Order entity continues to mean "tax to add" — when included_in_price
    is true it is 0 (the customer's total is unchanged) but the
    breakdown still records the implicit VAT for ETA submission.
"""

from dataclasses import dataclass, field
from typing import Any

# Egyptian VAT rate. The default is intentionally tied to the v1 launch
# market — when the platform expands to multi-country tax rules, this
# constant moves into a country-aware lookup keyed off the store's
# default_currency / billing country.
DEFAULT_EG_VAT_RATE: float = 0.14


@dataclass(frozen=True)
class TaxLineBreakdown:
    """Per-line tax allocation, used for invoice line items."""

    line_index: int
    taxable_amount_cents: int
    tax_cents: int
    rate: float


@dataclass(frozen=True)
class ResolvedTax:
    """Tax computation result for the order."""

    # The cents to ADD to the order total. Zero when prices are
    # tax-inclusive (the customer's total is unchanged).
    tax_to_add_cents: int = 0
    # The implicit VAT already baked into the listed prices, kept for
    # invoice / e-invoice reporting. Always populated, regardless of
    # the inclusive/exclusive setting.
    included_tax_cents: int = 0
    # The rate that was applied (after zone override, if any).
    rate: float = 0.0
    # True when the merchant has tax-inclusive pricing enabled.
    inclusive: bool = False
    # Per-line breakdown — kept for invoice rendering. Order matches
    # the input list_items order.
    breakdown: list[TaxLineBreakdown] = field(default_factory=list)


@dataclass(frozen=True)
class TaxLineInput:
    """Pure input shape — keeps the resolver decoupled from order DTOs."""

    unit_price_cents: int
    quantity: int


class TaxResolver:
    """Pure evaluator for a store's tax rules.

    Stateless on purpose so the same instance can be reused across
    requests (no connection pooling needed; no per-request init).
    """

    def __init__(self, *, default_rate: float = DEFAULT_EG_VAT_RATE) -> None:
        # Platform-wide fallback when the store hasn't customized its
        # tax_settings yet. Egyptian VAT for v1; revisit when the
        # platform onboards a non-MENA region.
        self._platform_default_rate = default_rate

    def resolve(
        self,
        *,
        store_settings: dict[str, Any] | None,
        line_items: list[TaxLineInput],
        discount_amount_cents: int = 0,
        destination_governorate: str | None = None,
        force_inclusive: bool = True,
    ) -> ResolvedTax:
        """Compute tax for the given cart against the store's settings.

        Args:
            store_settings: The store's `settings` JSONB (or any subset
                containing `tax_settings`). Pass None / {} for stores
                that haven't configured tax yet — the platform default
                rate applies.
            line_items: Per-line price + quantity. Discount is applied
                proportionally across lines so each row's breakdown is
                accurate for invoice display.
            discount_amount_cents: Total order-level discount (already
                resolved). Tax is computed on the post-discount taxable
                amount, matching Egyptian VAT practice.
            destination_governorate: ISO 3166-2 code (e.g. "EG-C"). When
                a `zone_overrides` entry matches, the override rate
                applies; otherwise the store default is used.
            force_inclusive: When True (platform default), VAT is always
                back-computed from the listed (inclusive) price; nothing
                is added on top. The merchant's ``included_in_price``
                setting is ignored. Set to False only for legacy callers
                that need the exclusive code path.
        """
        tax_settings = (store_settings or {}).get("tax_settings") or {}

        # Tax can be globally disabled by setting `enabled: false`.
        # Distinct from "rate=0" — disabled means we don't even emit
        # a breakdown (no VAT line on the invoice). Useful for stores
        # below the registration threshold.
        if tax_settings.get("enabled") is False:
            return ResolvedTax()

        rate = self._resolve_rate(tax_settings, destination_governorate)
        if rate <= 0:
            return ResolvedTax(rate=rate)

        # Platform-wide VAT-inclusive policy: merchants enter the final
        # retail price; we extract VAT from it instead of adding it.
        # ``force_inclusive=True`` is the default and overrides the
        # store-level setting.
        inclusive = (
            True
            if force_inclusive
            else bool(tax_settings.get("included_in_price", False))
        )

        # Compute pre-tax taxable amount per line, applying the order
        # discount proportionally so per-line breakdowns reconcile to
        # the order-level total. Discount is allocated cents-by-cents
        # against the largest residuals to avoid rounding drift.
        line_totals = [li.unit_price_cents * li.quantity for li in line_items]
        gross_subtotal = sum(line_totals)
        if gross_subtotal <= 0:
            return ResolvedTax(rate=rate, inclusive=inclusive)

        discount = max(0, min(discount_amount_cents, gross_subtotal))
        per_line_discount = self._allocate_discount(line_totals, discount)

        breakdown: list[TaxLineBreakdown] = []
        total_tax_cents = 0
        total_included_cents = 0

        for idx, (line_total, alloc_disc) in enumerate(
            zip(line_totals, per_line_discount, strict=True)
        ):
            taxable = max(0, line_total - alloc_disc)
            if inclusive:
                # Inclusive: the listed price ALREADY contains VAT.
                # Back-compute the tax component: tax = gross * rate /
                # (1 + rate). Round to nearest cent.
                line_tax = round(taxable * rate / (1.0 + rate))
                total_included_cents += line_tax
            else:
                # Exclusive: VAT is added on top.
                line_tax = round(taxable * rate)
                total_tax_cents += line_tax
            breakdown.append(
                TaxLineBreakdown(
                    line_index=idx,
                    taxable_amount_cents=taxable,
                    tax_cents=line_tax,
                    rate=rate,
                )
            )

        return ResolvedTax(
            tax_to_add_cents=total_tax_cents,
            included_tax_cents=total_included_cents,
            rate=rate,
            inclusive=inclusive,
            breakdown=breakdown,
        )

    # ─── Internals ─────────────────────────────────────────────────

    def _resolve_rate(
        self,
        tax_settings: dict[str, Any],
        destination_governorate: str | None,
    ) -> float:
        """Resolve the effective rate for the destination."""
        # Zone overrides win — a merchant can carve out a duty-free
        # zone or a higher-rate zone (uncommon in EG but legal for
        # multi-region rollouts). Match is case-insensitive against
        # the ISO code as it appears in the address.
        overrides = tax_settings.get("zone_overrides") or {}
        if destination_governorate and isinstance(overrides, dict):
            normalized = destination_governorate.upper().strip()
            for key, val in overrides.items():
                if str(key).upper().strip() == normalized:
                    return self._coerce_rate(val)

        # Fall back to the merchant's default_rate, then to the
        # platform-wide default (Egyptian VAT).
        if "default_rate" in tax_settings:
            return self._coerce_rate(tax_settings["default_rate"])
        return self._platform_default_rate

    @staticmethod
    def _coerce_rate(val: Any) -> float:
        """Normalize a rate stored as 0.14, "14%", "14", or 14 → 0.14.

        We accept several merchant-friendly inputs because the hub
        editor is not the only writer (CSV imports, API integrations).
        Anything that can't be coerced falls through to 0 — safer to
        skip tax than to over-charge from a parse error.
        """
        try:
            if isinstance(val, str):
                cleaned = val.strip().rstrip("%")
                num = float(cleaned)
            elif isinstance(val, int | float):
                num = float(val)
            else:
                return 0.0
        except (TypeError, ValueError):
            return 0.0
        # If the merchant entered "14" (no %) we treat it as a
        # percentage. Anything > 1 is interpreted as percent.
        if num > 1:
            num = num / 100.0
        if num < 0:
            return 0.0
        return num

    @staticmethod
    def _allocate_discount(line_totals: list[int], discount_cents: int) -> list[int]:
        """Allocate an order-level discount proportionally across lines.

        Uses the largest-residuals method so the per-line allocations
        sum to exactly the order-level discount (no off-by-one).
        """
        if discount_cents <= 0 or not line_totals:
            return [0] * len(line_totals)
        gross = sum(line_totals)
        if gross <= 0:
            return [0] * len(line_totals)

        raw = [(lt * discount_cents) / gross for lt in line_totals]
        floored = [int(r) for r in raw]
        residuals = sorted(
            range(len(line_totals)),
            key=lambda i: raw[i] - floored[i],
            reverse=True,
        )
        leftover = discount_cents - sum(floored)
        for i in residuals[:leftover]:
            floored[i] += 1
        return floored

"""Market registry — per-country defaults for multi-market support.

Single source of truth mapping an ISO 3166-1 alpha-2 country code to the
market's default currency, language, VAT rate, tax jurisdiction, and the
payment gateways offered to merchants there.

Why a registry instead of scattered ``if country == "EG"`` checks:
    Every market-specific default the platform needs at runtime — the
    onboarding currency/language, the tax rate the resolver applies, the
    gateways a merchant may configure — is derived from one table. Adding
    a market (Phase 2: Saudi Arabia) is a single entry here plus the
    implementations it references (tax service, gateway builders). Until a
    referenced gateway is registered in ``PROVIDER_BUILDERS`` it simply
    won't resolve, so listing it early is safe.

The ``country`` column on ``stores`` (added in Phase 0) is the key into
this table. Egypt is the fallback for unknown/missing codes because it is
the v1 launch market and every pre-existing store was backfilled to "EG".
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.value_objects.money import Currency

DEFAULT_COUNTRY = "EG"


@dataclass(frozen=True)
class Market:
    """Per-country market configuration."""

    # ISO 3166-1 alpha-2, e.g. "EG", "SA".
    country: str
    name: str
    # Currency a store in this market captures payment in by default.
    default_currency: Currency
    # Default storefront/admin language at onboarding ("ar" / "en").
    default_language: str
    # Standard VAT rate as a fraction (0.14 = 14%). Consumed by the tax
    # resolver as its platform-default rate for stores in this market.
    default_vat_rate: float
    # The country code the tax service keys off. Same as ``country`` today
    # but kept distinct so a market could borrow another jurisdiction's
    # rules without conflating identity with tax behaviour.
    tax_country_code: str
    # Payment-provider keys offered to merchants in this market, in
    # display order. Must match the keys in PROVIDER_BUILDERS once the
    # corresponding gateway lands (Phase 3 for the Saudi rails).
    payment_providers: tuple[str, ...]
    # IANA timezone used for store-local scheduling (e.g. the COD
    # Autopilot ship-digest hour, 004-cod-autopilot R-02). Stores carry
    # no timezone column; their market's zone is the best available
    # store-local clock.
    timezone: str = "Africa/Cairo"


EGYPT = Market(
    country="EG",
    name="Egypt",
    default_currency=Currency.EGP,
    default_language="ar",
    default_vat_rate=0.14,
    tax_country_code="EG",
    payment_providers=("paymob", "fawry", "kashier", "instapay", "cod"),
    timezone="Africa/Cairo",
)

SAUDI_ARABIA = Market(
    country="SA",
    name="Saudi Arabia",
    default_currency=Currency.SAR,
    default_language="ar",
    default_vat_rate=0.15,
    tax_country_code="SA",
    # Phase 3 implements these gateway services; until then they're
    # advertised by the registry but won't resolve at checkout.
    payment_providers=("moyasar", "hyperpay", "tabby", "tamara", "stcpay", "cod"),
    timezone="Asia/Riyadh",
)


MARKETS: dict[str, Market] = {m.country: m for m in (EGYPT, SAUDI_ARABIA)}


def get_market(country: str | None) -> Market:
    """Return the Market for a country code, falling back to Egypt.

    Accepts lower/upper case and surrounding whitespace. Unknown or
    missing codes resolve to Egypt (the v1 launch market) rather than
    raising — store creation must never hard-fail on a bad country.
    """
    if not country:
        return EGYPT
    return MARKETS.get(country.strip().upper(), EGYPT)


def supported_countries() -> list[str]:
    """ISO codes of all configured markets, for UI country pickers."""
    return list(MARKETS.keys())

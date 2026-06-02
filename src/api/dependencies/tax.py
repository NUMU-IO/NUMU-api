"""Tax-service factory — selects an ITaxService implementation by market.

Mirrors the payment-provider factory in ``payment.py``: a single place
that maps a store's market (``store.country`` → ``Market.tax_country_code``)
to the concrete tax service for that jurisdiction.

Today only Egypt is implemented. This factory is the registration seam
for Phase 2: when ``SaudiTaxService`` lands it is added to ``_TAX_SERVICES``
keyed by "SA" and every caller resolving by country picks it up with no
further wiring.

Note: the storefront checkout currently computes VAT through the stateless
``TaxResolver`` (see ``tax_resolver_for_country``), which is rate-driven and
sufficient for inclusive-pricing markets. ``ITaxService`` carries the richer
per-category / tax-id-validation behaviour needed for invoicing and ZATCA;
this factory exists so those call sites resolve the right jurisdiction.
"""

from __future__ import annotations

from src.application.services.market_registry import get_market
from src.core.interfaces.services.tax_service import ITaxService
from src.infrastructure.external_services.tax.egyptian_tax_service import (
    EgyptianTaxService,
)
from src.infrastructure.external_services.tax.saudi_tax_service import (
    SaudiTaxService,
)

# Keyed by ISO 3166-1 alpha-2 tax jurisdiction code. Instances are
# stateless, so a module-level singleton per jurisdiction is safe and
# avoids per-request construction.
_TAX_SERVICES: dict[str, ITaxService] = {
    "EG": EgyptianTaxService(),
    "SA": SaudiTaxService(),  # 15% VAT (ZATCA e-invoicing is program Phase 4)
}


def get_tax_service_for_country(country: str | None) -> ITaxService:
    """Return the tax service for a store's market.

    Falls back to the Egyptian service for jurisdictions that don't yet
    have a dedicated implementation, matching the registry's Egypt-first
    fallback so callers never hard-fail on an unimplemented market.
    """
    market = get_market(country)
    return _TAX_SERVICES.get(market.tax_country_code, _TAX_SERVICES["EG"])

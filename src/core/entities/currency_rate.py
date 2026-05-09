"""Currency rate entity — Phase 6.

Daily FX rates used for multi-currency presentment. The store still
captures payment in its base currency (EGP for MENA-first v1); these
rates power **display-only** conversion in the storefront — what
Shopify calls *presentment currencies*.

Source: ECB / openexchangerates.org / similar — wired via a daily
Celery task. For v1 we pre-seed a small set (EGP/USD/EUR/SAR/AED)
and refresh on demand.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.core.entities.base import BaseEntity


class CurrencyRate(BaseEntity):
    """An exchange rate from base→target."""

    base: str  # ISO 4217, e.g. "EGP"
    target: str  # ISO 4217, e.g. "USD"
    rate: Decimal  # e.g. 0.0205 (1 EGP = 0.0205 USD)
    fetched_at: datetime

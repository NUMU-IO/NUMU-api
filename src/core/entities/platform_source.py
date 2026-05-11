"""Platform source discriminator (backend-026 / spec 017).

Tags every order-shaped row with the platform it originated from.
Default ``shopify`` keeps the existing implicit-Shopify world working
without any code changes; future platform adapters set their own value.

The enum is intentionally values-based (lowercase strings) per the
project's StrEnum + ``values_callable`` convention — see
`memory/MEMORY.md` for the values_callable pitfall details.
"""

from __future__ import annotations

from enum import StrEnum


class OrderSource(StrEnum):
    """Where this order / customer / shipment originated."""

    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"
    SALLA = "salla"
    ZID = "zid"
    NUMU_NATIVE = "numu_native"
    TIKTOK_SHOP = "tiktok_shop"


# The lowercase string forms used by the DB enum type. Centralised here
# so the migration + the SQLAlchemy column declaration agree on the set.
ORDER_SOURCE_VALUES: tuple[str, ...] = tuple(m.value for m in OrderSource)

DEFAULT_ORDER_SOURCE: OrderSource = OrderSource.SHOPIFY

"""Is this store ready to take orders?

Four conditions, each read from the real state rather than from a checklist
someone ticked: a product a shopper can actually buy, a shipping price, a way
to pay, and a number to call. The dashboard meter, the go-live step and any
future gate all ask this one function, so they cannot disagree.
"""

from __future__ import annotations

from typing import Any

# Online methods that count once enabled AND configured. COD counts on its own
# (it is on by default); these are for a store that switched COD off.
_ONLINE_METHODS = (
    "fawry",
    "fawaterak",
    "paymob",
    "kashier",
    "instapay",
    "vodafone_cash",
    "bank_transfer",
    "moyasar",
)


def payment_ready(settings: dict[str, Any] | None) -> bool:
    payment = (settings or {}).get("payment") or {}
    if (payment.get("cod") or {}).get("enabled", True):
        return True
    return any(
        (payment.get(m) or {}).get("enabled")
        and (payment.get(m) or {}).get("is_configured")
        for m in _ONLINE_METHODS
    )


async def store_readiness(session, store) -> dict[str, Any]:
    """``{"items": [{key, done}], "done": n, "total": 4, "ready": bool}``."""
    from sqlalchemy import exists, select

    from src.core.entities.product import ProductStatus
    from src.infrastructure.database.models.tenant.product import ProductModel
    from src.infrastructure.database.models.tenant.shipping_rate import (
        ShippingRateModel,
    )
    from src.infrastructure.database.models.tenant.shipping_zone import (
        ShippingZoneModel,
    )

    settings = store.settings or {}
    sellable = exists().where(
        ProductModel.store_id == store.id,
        ProductModel.status == ProductStatus.ACTIVE,
        # Seeded samples are a preview, not stock (same markers the seeder uses).
        ~(
            ProductModel.slug.startswith("demo-")
            & ProductModel.attributes["demo_seed"].as_boolean().is_(True)
        ),
        (ProductModel.quantity > 0)
        | ProductModel.attributes["continue_selling_when_out_of_stock"]
        .as_boolean()
        .is_(True),
    )
    priced_zone = exists().where(
        ShippingZoneModel.store_id == store.id,
        ShippingZoneModel.is_active.is_(True),
        ShippingRateModel.zone_id == ShippingZoneModel.id,
        ShippingRateModel.is_active.is_(True),
    )
    has_product, has_zone = (await session.execute(select(sellable, priced_zone))).one()

    restricted = bool((settings.get("shipping") or {}).get("restrict_to_zones"))
    items = [
        {"key": "product_live", "done": bool(has_product)},
        # An unrestricted (older) store ships at its fallback rate, so it can
        # take an order; a restricted one needs a priced zone first.
        {"key": "shipping_priced", "done": bool(has_zone) or not restricted},
        {"key": "payment_method", "done": payment_ready(settings)},
        {"key": "contact_number", "done": bool(store.contact_phone)},
    ]
    done = sum(item["done"] for item in items)
    return {
        "items": items,
        "done": done,
        "total": len(items),
        "ready": done == len(items),
    }

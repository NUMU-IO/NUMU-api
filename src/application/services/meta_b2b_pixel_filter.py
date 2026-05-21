"""Wave 4 Phase 25 — B2B / wholesale pixel exclusion.

**Gate:** NUMU has no B2B / wholesale product as of 2026-05-17. This
module is a code-ready filter the B2B team can apply once the
wholesale-pricing feature ships.

The problem: when NUMU adds B2B (gated wholesale pricing visible only
to approved buyers), the Meta Pixel will see two parallel realities:
the public catalog at retail prices and the gated catalog at
wholesale prices. Without filtering, wholesale prices leak into
public dynamic ads (Shopify documents this exact gotcha for their
own Plus B2B — see plan reference §15).

The filter exposes two predicates the storefront's ``fireMetaEvent``
chain (and the backend dispatcher) can call before firing:

  * ``should_skip_for_b2b_role(customer)`` — when the customer is a
    B2B-tagged role (e.g. ``wholesale_buyer``), skip Pixel firing
    entirely. Their orders shouldn't fuel Meta's optimization
    against retail customers.

  * ``should_skip_for_b2b_product(product)`` — when the product is
    marked B2B-only (via a ``b2b_only`` attribute / tag), skip
    fireMetaEvent for that product even if the customer is anonymous.
    Prevents the gated-pricing leak.

Both default to ``False`` (no filtering) when the feature flag isn't
set on the store, so behavior is unchanged for stores without B2B.
"""

from __future__ import annotations

from typing import Any


def is_b2b_enabled(store_settings: dict | None) -> bool:
    """True iff the merchant has enabled the B2B / wholesale feature.

    Gated by ``settings.b2b.enabled`` on the store. When false, both
    predicate helpers below return False unconditionally so non-B2B
    stores pay zero filtering cost.
    """
    if not store_settings:
        return False
    b2b_cfg = store_settings.get("b2b") or {}
    return bool(b2b_cfg.get("enabled"))


def should_skip_for_b2b_role(store_settings: dict | None, customer: Any) -> bool:
    """Skip Pixel firing when the customer holds a B2B-tagged role.

    Recognized roles (in order of frequency):
      * ``wholesale_buyer``
      * ``b2b_buyer``
      * Any role listed in ``settings.b2b.excluded_roles``
    """
    if not is_b2b_enabled(store_settings):
        return False
    if customer is None:
        return False
    role = getattr(customer, "role", None) or (
        customer.get("role") if isinstance(customer, dict) else None
    )
    if not role:
        return False
    role = str(role).lower()
    builtins = {"wholesale_buyer", "b2b_buyer", "wholesale"}
    if role in builtins:
        return True
    extras = (store_settings or {}).get("b2b", {}).get("excluded_roles") or []
    return role in {str(r).lower() for r in extras}


def should_skip_for_b2b_product(store_settings: dict | None, product: Any) -> bool:
    """Skip Pixel firing when the product is marked B2B-only.

    Recognized markers (a product is B2B-only when ANY apply):
      * ``product.b2b_only == True``
      * ``"b2b_only"`` in ``product.tags``
      * ``"b2b"`` in ``product.tags``
      * ``product.attributes["b2b_only"] == True``
    """
    if not is_b2b_enabled(store_settings):
        return False
    if product is None:
        return False

    # Attribute on the entity
    if getattr(product, "b2b_only", False):
        return True

    # Tags array
    tags = getattr(product, "tags", None) or (
        product.get("tags") if isinstance(product, dict) else None
    )
    if tags:
        tag_set = {str(t).lower() for t in tags}
        if "b2b" in tag_set or "b2b_only" in tag_set or "wholesale" in tag_set:
            return True

    # attributes JSONB
    attrs = getattr(product, "attributes", None) or (
        product.get("attributes") if isinstance(product, dict) else None
    )
    if isinstance(attrs, dict) and attrs.get("b2b_only"):
        return True

    return False

"""Typed per-store SEO settings.

Stored under `Store.settings["seo"]`. The free-form `settings` blob stays as
the persistence shape (so legacy keys remain untouched), but the storefront
and admin layer read/write this typed sub-shape exclusively.

Every field is optional with safe defaults. Null / missing means
"use storefront default behavior" — e.g. `robots_indexing_enabled = True`
means indexable, which is what we want before the merchant has touched the
SEO tab.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BusinessTypeLiteral = Literal[
    "Organization",
    "Store",
    "FashionStore",
    "JewelryStore",
    "ShoeStore",
    "ClothingStore",
    "BeautySalon",
    # A beauty/cosmetics RETAILER is not a `BeautySalon` (schema.org models that
    # as a service business with an address). `HealthAndBeautyBusiness` is the
    # honest subtype for a store that sells products.
    "HealthAndBeautyBusiness",
    "ElectronicsStore",
    "FurnitureStore",
    # Broader than `FurnitureStore` — the onboarding "home" niche covers decor,
    # kitchenware and textiles, not only furniture.
    "HomeGoodsStore",
    "GroceryStore",
    "ConvenienceStore",
    "Bakery",
    "BookStore",
    "ToyStore",
    "SportingGoodsStore",
    "PetStore",
    "OfficeEquipmentStore",
    "MobilePhoneStore",
]


class StoreSeoSettings(BaseModel):
    """Per-store SEO overrides + verification tokens.

    Read out of `Store.settings["seo"]`. Merchant dashboard writes update the
    same nested blob via the existing store-settings PATCH endpoint.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Branding overrides for the storefront generateMetadata helper.
    seo_title: str | None = Field(default=None, max_length=70)
    seo_description: str | None = Field(default=None, max_length=160)
    social_image_url: str | None = Field(default=None, max_length=2048)

    # Indexation opt-in. True = indexable (default). False = robots noindex
    # and Disallow:/ in robots.txt regardless of store status.
    robots_indexing_enabled: bool = True

    # Search engine site-ownership verification tokens.
    google_site_verification: str | None = Field(default=None, max_length=128)
    bing_site_verification: str | None = Field(default=None, max_length=128)

    # Optional Schema.org subtype for the Organization JSON-LD emitted on
    # the storefront home page. Defaults to plain "Organization" so we don't
    # falsely claim a physical location.
    business_type: BusinessTypeLiteral | None = None

    # Toggles for richer Product JSON-LD on the PDP. When the merchant has
    # actually committed to the policy, surface it; otherwise omit so we
    # don't claim something Google might disprove with a test order.
    has_return_policy_30d: bool = False

    # Gates the `ar` hreflang on the storefront. Bilingual chrome is not a
    # bilingual catalogue: until product names/descriptions are actually
    # translated, /ar serves English copy under an Arabic shell, and
    # advertising ar-* for that is scaled-content territory. Opt-in.
    arabic_content_ready: bool = False

    @field_validator(
        "seo_title",
        "seo_description",
        "social_image_url",
        "google_site_verification",
        "bing_site_verification",
        mode="after",
    )
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        trimmed = v.strip()
        return trimmed or None


# The hub's Preferences page wrote these at the TOP level of `settings` while
# the storefront reads only `settings["seo"]`, so merchant input was silently
# discarded. Recovered here so stores are correct before the backfill runs.
_LEGACY_KEY_MAP = {
    "seo_title": "seo_title",
    "seo_description": "seo_description",
    "social_image_url": "social_image_url",
}


def _truncate(value: str, limit: int) -> str:
    """Trim to ``limit`` on a word boundary where possible.

    The legacy keys were never length-validated. Truncating rather than letting
    Pydantic reject matters: a ValidationError here would 500 the whole store
    payload over a long meta description.
    """
    value = value.strip()
    if len(value) <= limit:
        return value
    clipped = value[:limit]
    spaced = clipped.rsplit(" ", 1)[0]
    # Only honour the word boundary if it keeps most of the budget; a long
    # unbroken string (no spaces) must still be cut.
    return (spaced if len(spaced) >= limit * 0.6 else clipped).rstrip()


def legacy_seo_fields(raw_settings: object) -> dict[str, str]:
    """The legacy top-level ``seo_*`` keys, trimmed and length-clamped."""
    if not isinstance(raw_settings, dict):
        return {}
    recovered: dict[str, str] = {}
    for legacy_key, field_name in _LEGACY_KEY_MAP.items():
        value = raw_settings.get(legacy_key)
        if not isinstance(value, str) or not value.strip():
            continue
        field = StoreSeoSettings.model_fields[field_name]
        limit = next(
            (m.max_length for m in field.metadata if hasattr(m, "max_length")), None
        )
        recovered[field_name] = _truncate(value, limit) if limit else value.strip()
    return recovered


def normalize_store_seo(raw_settings: object) -> dict:
    """The typed SEO block for a store, with legacy flat-key fallback.

    Fallback is per FIELD, not per block: a typed value wins, a typed *null*
    defers to the legacy key. Never raises — a malformed blob degrades to
    schema defaults rather than breaking the storefront payload.
    """
    if not isinstance(raw_settings, dict):
        return StoreSeoSettings().model_dump()

    raw_seo = raw_settings.get("seo")
    typed: dict = {}
    if isinstance(raw_seo, dict):
        try:
            typed = StoreSeoSettings.model_validate(raw_seo).model_dump()
        except Exception:
            typed = {}

    merged = {**typed}
    for field_name, value in legacy_seo_fields(raw_settings).items():
        if merged.get(field_name) is None:
            merged[field_name] = value

    try:
        return StoreSeoSettings.model_validate(merged).model_dump()
    except Exception:
        return StoreSeoSettings().model_dump()

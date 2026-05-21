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
    "ElectronicsStore",
    "FurnitureStore",
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

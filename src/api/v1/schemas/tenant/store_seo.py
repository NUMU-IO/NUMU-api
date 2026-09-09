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


class FaqEntry(BaseModel):
    """One question/answer pair, emitted as FAQPage JSON-LD.

    Kept deliberately small. A FAQ is only useful to an answer engine when it
    is the question a shopper actually typed and an answer that stands alone
    out of context, so there is nowhere for a longer body to go.
    """

    model_config = ConfigDict(extra="ignore")

    question: str = Field(max_length=200)
    answer: str = Field(max_length=800)

    @field_validator("question", "answer", mode="after")
    @classmethod
    def _required_text(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("FAQ question and answer cannot be blank")
        return trimmed


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

    # ── AEO: being the answer, not just a result ──────────────────────────
    # Answer engines quote a passage; they do not summarise a whole page. A
    # store with nothing quotable gets skipped in favour of one that wrote a
    # straight answer to "what does this shop sell".
    short_answer: str | None = Field(default=None, max_length=320)

    # FAQ pairs, emitted as FAQPage JSON-LD. The single biggest AEO lever
    # available to a store: it is the only structured place to say "do you
    # deliver to Aswan" in the words a shopper actually asks it.
    faqs: list[FaqEntry] = Field(default_factory=list, max_length=20)

    # ── GEO: being citable by generative engines ──────────────────────────
    # Whether GPTBot, ClaudeBot, PerplexityBot and Google-Extended may crawl.
    # Default True and deliberately separate from `robots_indexing_enabled`:
    # these are different decisions. A merchant can want Google's index and
    # not want their catalogue in a training set, or the reverse — being
    # cited by an assistant that sends buyers is worth more to most stores
    # than the copy is worth withholding.
    ai_crawlers_allowed: bool = True

    # Whether the store's text and photography may be used as TRAINING data,
    # expressed as the `ai-train` Content-Signal. Deliberately a third
    # decision, not a consequence of the two above: being read to answer a
    # shopper's question today and being absorbed into a model's weights
    # forever are different bargains, and a merchant can rationally want the
    # first without the second.
    #
    # Defaults to False, unlike its siblings. Everywhere else here the default
    # favours being found, because obscurity costs a shop sales. This one is
    # the merchant's own product photography and copy, and the merchant gets
    # nothing back for it — so silence means no. Was hardcoded `ai-train=no`
    # platform-wide in the storefront; this makes it theirs to change.
    ai_training_allowed: bool = False

    # Serve /llms.txt — a short, plain-text map of the store for models that
    # cannot afford to crawl the whole site.
    llms_txt_enabled: bool = True

    # ── Entity graph: what makes the store a known thing ──────────────────
    # Organization.sameAs. Profiles that corroborate the store is real; this
    # is how an engine decides two mentions are the same business.
    same_as: list[str] = Field(default_factory=list, max_length=10)
    contact_email: str | None = Field(default=None, max_length=254)
    contact_phone: str | None = Field(default=None, max_length=32)
    # Where the store actually ships, for "near me" and locality answers.
    area_served: list[str] = Field(default_factory=list, max_length=20)
    founding_year: int | None = Field(default=None, ge=1900, le=2100)

    @field_validator("same_as", "area_served", mode="after")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        """Drop blanks and duplicates, preserving order."""
        seen: set[str] = set()
        out: list[str] = []
        for item in v or []:
            trimmed = (item or "").strip()
            if trimmed and trimmed not in seen:
                seen.add(trimmed)
                out.append(trimmed)
        return out

    @field_validator("same_as", mode="after")
    @classmethod
    def _https_profiles(cls, v: list[str]) -> list[str]:
        """sameAs must be absolute URLs; a bare handle is not an identity."""
        return [u for u in v if u.startswith(("https://", "http://"))]

    @field_validator(
        "seo_title",
        "seo_description",
        "social_image_url",
        "google_site_verification",
        "bing_site_verification",
        "short_answer",
        "contact_email",
        "contact_phone",
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

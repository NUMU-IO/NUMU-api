"""SEO Phase 4 regression tests.

Drives the schema + serializer directly with stub store objects — the
full FastAPI app is not booted here. Same pattern as
`test_tracking_async.py`: keep tests sync, stand up stubs that match
the shapes the production code reads.

Covers:
- ProductResponse carries `seo_title` / `seo_description` (the bug
  that blocked Phase 1's PDP from using merchant-set SEO overrides).
- StoreSeoSettings normalizes the typed `seo` block, trims, and
  defaults `robots_indexing_enabled = True`.
- `_serialize_public_store` exposes the `seo` block alongside the raw
  `settings` blob.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.api.v1.routes.storefront.public import _serialize_public_store
from src.api.v1.schemas.tenant.product import ProductResponse
from src.api.v1.schemas.tenant.store_seo import StoreSeoSettings


@dataclass
class _StubStore:
    """Minimal stand-in for the Store entity, shaped to match
    `_serialize_public_store`'s attribute reads."""

    name: str = "Sawsaw"
    slug: str = "sawsaw"
    subdomain: str = "sawsaw"
    custom_domain: str | None = None
    description: str | None = "Women's fashion."
    logo_url: str | None = "https://cdn/logo.png"
    banner_url: str | None = "https://cdn/banner.png"
    status: str = "active"
    settings: dict[str, Any] = field(default_factory=dict)
    theme_settings: dict[str, Any] = field(default_factory=dict)
    business_hours: dict[str, Any] = field(default_factory=dict)
    default_currency: str = "EGP"
    default_language: str = "ar"
    social_links: dict[str, Any] = field(default_factory=dict)
    use_nextjs_storefront: bool = True
    id: Any = field(default_factory=uuid4)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_minimal_product_response_kwargs() -> dict[str, Any]:
    """All required ProductResponse fields. Tests append seo_* on top."""
    return {
        "id": str(uuid4()),
        "store_id": str(uuid4()),
        "name": "Silver Bracelet",
        "slug": "silver-bracelet",
        "sku": "SB-1",
        "description": "Pretty thing.",
        "short_description": "Bracelet",
        "product_type": "physical",
        "status": "active",
        "price": "299.00",
        "price_currency": "EGP",
        "compare_at_price": None,
        "cost_price": None,
        "quantity": 5,
        "is_in_stock": True,
        "is_low_stock": False,
        "is_on_sale": False,
        "images": ["https://cdn/p1.jpg"],
        "category_id": None,
        "tags": [],
        "attributes": {},
        "options": [],
        "variants": [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


# ── ProductResponse SEO fields ────────────────────────────────────────────


def test_product_response_accepts_seo_title_and_description() -> None:
    """The fields land on the response when the entity has them set."""
    kwargs = _build_minimal_product_response_kwargs()
    kwargs["seo_title"] = "Custom title"
    kwargs["seo_description"] = "Custom description for SEO."
    resp = ProductResponse(**kwargs)
    assert resp.seo_title == "Custom title"
    assert resp.seo_description == "Custom description for SEO."


def test_product_response_seo_fields_default_to_none() -> None:
    """Legacy rows without SEO data still validate; storefront falls back
    to product name / short_description per Phase 1."""
    resp = ProductResponse(**_build_minimal_product_response_kwargs())
    assert resp.seo_title is None
    assert resp.seo_description is None


def test_product_response_rejects_overlong_seo_title() -> None:
    """Pydantic enforces the 70-char SEO title limit Google recommends."""
    kwargs = _build_minimal_product_response_kwargs()
    kwargs["seo_title"] = "a" * 71
    try:
        ProductResponse(**kwargs)
    except Exception as exc:  # pydantic.ValidationError
        assert "seo_title" in str(exc).lower() or "70" in str(exc)
        return
    raise AssertionError("ProductResponse should have rejected a 71-char SEO title")


# ── StoreSeoSettings normalization ───────────────────────────────────────


def test_store_seo_settings_defaults_to_indexable() -> None:
    seo = StoreSeoSettings.model_validate({})
    assert seo.robots_indexing_enabled is True
    assert seo.seo_title is None
    assert seo.business_type is None
    assert seo.has_return_policy_30d is False


def test_store_seo_settings_normalizes_strings() -> None:
    """Whitespace-only strings collapse to None so storefront helpers can
    treat falsy as 'use default'."""
    seo = StoreSeoSettings.model_validate({
        "seo_title": "   ",
        "seo_description": "  Real description.  ",
        "google_site_verification": "",
    })
    assert seo.seo_title is None
    assert seo.seo_description == "Real description."
    assert seo.google_site_verification is None


def test_store_seo_settings_ignores_unknown_keys() -> None:
    """`extra="ignore"` so a future field rollout from the dashboard
    doesn't 422 every store read."""
    seo = StoreSeoSettings.model_validate({
        "seo_title": "ok",
        "future_field_we_havent_invented_yet": "value",
    })
    assert seo.seo_title == "ok"


def test_store_seo_settings_business_type_is_validated() -> None:
    """Schema.org subtype is a Literal — anything outside the allowlist is
    rejected so we don't emit broken JSON-LD on the storefront."""
    try:
        StoreSeoSettings.model_validate({"business_type": "TacoTruck"})
    except Exception:
        return
    raise AssertionError("StoreSeoSettings should reject an unsupported business_type.")


# ── _serialize_public_store seo block ────────────────────────────────────


def test_serialize_public_store_emits_seo_block_with_defaults() -> None:
    """Stores that have never opened the SEO tab get a normalized
    default block (robots_indexing_enabled = True, etc.)."""
    store = _StubStore()
    payload = _serialize_public_store(store)
    assert "seo" in payload
    assert payload["seo"]["robots_indexing_enabled"] is True
    assert payload["seo"]["seo_title"] is None
    assert payload["seo"]["business_type"] is None


def test_serialize_public_store_normalizes_merchant_seo_overrides() -> None:
    """Merchant-set SEO under settings.seo flows through the typed model."""
    store = _StubStore(
        settings={
            "seo": {
                "seo_title": "Sawsaw — Cairo fashion",
                "seo_description": "Premium curated pieces.",
                "robots_indexing_enabled": False,
                "google_site_verification": "g-token",
                "business_type": "FashionStore",
            }
        }
    )
    payload = _serialize_public_store(store)
    assert payload["seo"]["seo_title"] == "Sawsaw — Cairo fashion"
    assert payload["seo"]["robots_indexing_enabled"] is False
    assert payload["seo"]["business_type"] == "FashionStore"


def test_serialize_public_store_preserves_raw_settings_for_legacy_readers() -> None:
    """Phase 4 adds `seo` but keeps `settings.seo` untouched so theme
    settings consumers and tracking config still work."""
    store = _StubStore(
        settings={
            "tracking": {"meta": {"domain_verification_token": "fb-abc"}},
            "seo": {"seo_title": "test"},
        }
    )
    payload = _serialize_public_store(store)
    assert payload["settings"]["seo"]["seo_title"] == "test"
    assert (
        payload["settings"]["tracking"]["meta"]["domain_verification_token"] == "fb-abc"
    )


def test_serialize_public_store_handles_non_dict_seo_safely() -> None:
    """A corrupt store row (settings.seo accidentally a string) should not
    500 — it should fall through to defaults."""
    store = _StubStore(settings={"seo": "not a dict"})
    payload = _serialize_public_store(store)
    assert payload["seo"]["robots_indexing_enabled"] is True


def test_serialize_public_store_handles_none_settings() -> None:
    """Brand-new stores with no settings.* keys still serialize cleanly."""
    store = _StubStore(settings={})
    store.settings = None  # type: ignore[assignment]
    payload = _serialize_public_store(store)
    assert payload["seo"]["robots_indexing_enabled"] is True

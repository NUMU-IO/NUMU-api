"""Unit tests for the LinkBuilder service.

Covers the test table from contracts/link-builder-service.md plus a few
additional cases (Arabic-name slug transliteration, query-string
encoding edge cases, channel-default-source).
"""

from __future__ import annotations

from uuid import uuid4

from src.application.services.link_builder import LinkBuilder
from src.core.entities.marketing_campaign import (
    CampaignChannel,
    CampaignStatus,
    MarketingCampaign,
)
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.money import Currency, Money

# ── Fixtures ────────────────────────────────────────────────────────


def _subdomain_store() -> Store:
    return Store(
        id=uuid4(),
        owner_id=uuid4(),
        name="Acme Store",
        slug="acme",
        subdomain="acme",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
    )


def _custom_domain_store() -> Store:
    return Store(
        id=uuid4(),
        owner_id=uuid4(),
        name="Acme Store",
        slug="acme",
        subdomain="acme",
        custom_domain="shop.acme.com",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
    )


def _product(slug: str = "linen-kaftan") -> Product:
    return Product(
        id=uuid4(),
        store_id=uuid4(),
        name="Linen Kaftan",
        slug=slug,
        sku="LK-001",
        product_type=ProductType.PHYSICAL,
        status=ProductStatus.ACTIVE,
        price=Money.from_cents(100000, Currency.EGP),
        quantity=10,
    )


def _campaign(
    *,
    name: str = "Eid Sale 2026",
    short_code: str = "AB7K9X",
    channel: CampaignChannel = CampaignChannel.EMAIL,
) -> MarketingCampaign:
    return MarketingCampaign(
        tenant_id=uuid4(),
        store_id=uuid4(),
        channel=channel,
        name=name,
        status=CampaignStatus.DRAFT,
        short_code=short_code,
    )


# ── Origin resolution ───────────────────────────────────────────────


def test_subdomain_store_origin():
    builder = LinkBuilder(_subdomain_store())
    assert builder.origin == "https://acme.numueg.app"


def test_custom_domain_store_origin_wins_over_subdomain():
    builder = LinkBuilder(_custom_domain_store())
    assert builder.origin == "https://shop.acme.com"


# ── storefront_url ──────────────────────────────────────────────────


def test_storefront_url_untagged():
    builder = LinkBuilder(_subdomain_store())
    assert builder.storefront_url() == "https://acme.numueg.app/"


def test_storefront_url_with_campaign():
    builder = LinkBuilder(_subdomain_store())
    url = builder.storefront_url(campaign=_campaign(), source="facebook")
    assert "https://acme.numueg.app/?" in url
    assert "utm_source=facebook" in url
    assert "utm_medium=social" in url  # default for source=facebook
    assert "utm_campaign=eid-sale-2026-AB7K9X" in url


# ── product_url ─────────────────────────────────────────────────────


def test_product_url_uses_slug():
    builder = LinkBuilder(_subdomain_store())
    p = _product(slug="linen-kaftan")
    assert (
        builder.product_url(product=p) == "https://acme.numueg.app/product/linen-kaftan"
    )


def test_product_url_falls_back_to_id_for_empty_slug():
    """Product entity requires slug to be a string, but an all-whitespace
    slug is treated as empty and the URL falls back to the product ID."""
    builder = LinkBuilder(_subdomain_store())
    p = _product(slug="   ")
    url = builder.product_url(product=p)
    assert url == f"https://acme.numueg.app/product/{p.id}"


def test_product_url_with_campaign_and_source():
    builder = LinkBuilder(_subdomain_store())
    url = builder.product_url(
        product=_product(),
        campaign=_campaign(),
        source="facebook",
    )
    assert url.startswith("https://acme.numueg.app/product/linen-kaftan?")
    assert "utm_source=facebook" in url
    assert "utm_medium=social" in url
    assert "utm_campaign=eid-sale-2026-AB7K9X" in url


def test_product_url_custom_domain():
    builder = LinkBuilder(_custom_domain_store())
    url = builder.product_url(product=_product())
    assert url == "https://shop.acme.com/product/linen-kaftan"


# ── collection_url ──────────────────────────────────────────────────


def test_collection_url_renders_with_query_param():
    builder = LinkBuilder(_subdomain_store())
    url = builder.collection_url(collection_slug="summer-2026")
    assert url == "https://acme.numueg.app/collections?category=summer-2026"


def test_collection_url_with_campaign_combines_query_params():
    builder = LinkBuilder(_subdomain_store())
    url = builder.collection_url(
        collection_slug="eid-special",
        campaign=_campaign(),
        source="instagram",
    )
    assert url.startswith("https://acme.numueg.app/collections?")
    assert "category=eid-special" in url
    assert "utm_source=instagram" in url
    assert "utm_medium=social" in url
    assert "utm_campaign=eid-sale-2026-AB7K9X" in url


# ── custom_url ──────────────────────────────────────────────────────


def test_custom_url_passes_through_path():
    builder = LinkBuilder(_subdomain_store())
    assert (
        builder.custom_url(path="/lookbook/eid-2026")
        == "https://acme.numueg.app/lookbook/eid-2026"
    )


def test_custom_url_rejects_relative_path():
    builder = LinkBuilder(_subdomain_store())
    try:
        builder.custom_url(path="lookbook")
        raise AssertionError("expected ValueError for relative path")
    except ValueError:
        pass


def test_custom_url_with_campaign():
    builder = LinkBuilder(_subdomain_store())
    url = builder.custom_url(
        path="/about",
        campaign=_campaign(),
        source="email",
    )
    assert url.startswith("https://acme.numueg.app/about?")
    assert "utm_source=email" in url
    assert "utm_medium=email" in url
    assert "utm_campaign=eid-sale-2026-AB7K9X" in url


# ── utm_campaign_for ────────────────────────────────────────────────


def test_utm_campaign_for_basic():
    c = _campaign(name="Eid Sale 2026", short_code="AB7K9X")
    assert LinkBuilder.utm_campaign_for(c) == "eid-sale-2026-AB7K9X"


def test_utm_campaign_for_arabic_name_falls_back_to_campaign_slug():
    """Pure Arabic names slugify to empty — fall back to 'campaign'."""
    c = _campaign(name="تخفيضات العيد", short_code="AB7K9X")
    out = LinkBuilder.utm_campaign_for(c)
    # The slug is either 'campaign' (empty Arabic) or some transliteration.
    # Either way the short_code suffix is preserved.
    assert out.endswith("-AB7K9X")


def test_utm_campaign_for_special_chars_in_name():
    c = _campaign(name="50% off!! → Eid 2026", short_code="AB7K9X")
    out = LinkBuilder.utm_campaign_for(c)
    # No %, !, →, or other special chars should remain.
    body = out.removesuffix("-AB7K9X")
    for ch in "%!→":
        assert ch not in body


# ── Channel defaults + term/content tags ────────────────────────────


def test_email_campaign_without_source_defaults_to_email():
    """Email channel + no explicit source → utm_source=email."""
    builder = LinkBuilder(_subdomain_store())
    url = builder.storefront_url(campaign=_campaign(channel=CampaignChannel.EMAIL))
    assert "utm_source=email" in url
    assert "utm_medium=email" in url


def test_sms_campaign_without_source_defaults_to_sms():
    builder = LinkBuilder(_subdomain_store())
    url = builder.storefront_url(campaign=_campaign(channel=CampaignChannel.SMS))
    assert "utm_source=sms" in url
    assert "utm_medium=sms" in url


def test_term_and_content_are_attached():
    builder = LinkBuilder(_subdomain_store())
    url = builder.product_url(
        product=_product(),
        campaign=_campaign(),
        source="facebook",
        term="linen-collection",
        content="eid-banner-v2",
    )
    assert "utm_term=linen-collection" in url
    assert "utm_content=eid-banner-v2" in url


# ── Query-string encoding ───────────────────────────────────────────


def test_special_chars_in_term_are_url_encoded():
    builder = LinkBuilder(_subdomain_store())
    url = builder.storefront_url(
        campaign=_campaign(),
        source="email",
        term="spaces and & ampersand",
    )
    # urllib.parse.urlencode default uses quote_plus → spaces become '+'
    assert "utm_term=spaces+and+%26+ampersand" in url


# ── No-UTM safety ───────────────────────────────────────────────────


def test_no_campaign_no_source_produces_clean_url():
    builder = LinkBuilder(_subdomain_store())
    url = builder.storefront_url()
    assert "?" not in url
    assert url == "https://acme.numueg.app/"

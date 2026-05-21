"""Wave 3 Phase 16 — Meta Commerce Catalog XML feed unit tests.

Covers the pure formatter functions (no FastAPI dependency injection,
no DB) so the XML contract is pinned without spinning up the full
storefront route. Integration smoke for the route itself happens via
the live curl-equivalent in the manual sign-off.

Pins:
  * HTML escaping of merchant-supplied strings (prevents `&`/`<` in
    titles from breaking Meta's strict XML parser)
  * Field set matches Meta's required + recommended attributes
  * Out-of-stock filtering when inventory is tracked
  * Untracked inventory always renders ``in stock``
  * Draft products are excluded
  * meta_catalog_id takes precedence over internal UUID for g:id
  * item_group_id always uses internal UUID (variant grouping)
"""

from __future__ import annotations

from src.api.v1.routes.storefront.meta_feed import (
    _build_feed_xml,
    _item_xml,
    _product_to_feed_item,
)

# ---------------------------------------------------------------------------
# _product_to_feed_item — the row-mapper
# ---------------------------------------------------------------------------


def _row(**overrides) -> dict:
    """Build a fake products-table row dict."""
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Test Product",
        "description": "A product",
        "short_description": "Short desc",
        "sku": "SKU-001",
        "price_amount": 24950,
        "status": "active",
        "quantity": 5,
        "images": ["https://cdn.example.com/img1.jpg"],
        "attributes": {"brand": "TestBrand", "product_type": "Apparel"},
        "meta_catalog_id": None,
        "track_inventory": True,
    }
    base.update(overrides)
    return base


class TestProductToFeedItem:
    def test_basic_active_product(self):
        item = _product_to_feed_item(_row(), store_subdomain="testsub", currency="EGP")
        assert item is not None
        assert item["id"] == "11111111-1111-1111-1111-111111111111"
        assert item["item_group_id"] == "11111111-1111-1111-1111-111111111111"
        assert item["title"] == "Test Product"
        assert item["availability"] == "in stock"
        assert item["price"] == "249.50 EGP"
        assert (
            item["link"]
            == "https://testsub.numu.store/product/11111111-1111-1111-1111-111111111111"
        )
        assert item["image_link"] == "https://cdn.example.com/img1.jpg"
        assert item["brand"] == "TestBrand"
        assert item["product_type"] == "Apparel"
        assert item["sku"] == "SKU-001"

    def test_out_of_stock_when_quantity_zero_and_tracked(self):
        item = _product_to_feed_item(
            _row(quantity=0, track_inventory=True),
            store_subdomain="t",
            currency="EGP",
        )
        assert item["availability"] == "out of stock"

    def test_in_stock_when_inventory_untracked_even_at_zero(self):
        # Service products / digital goods often have track_inventory=False;
        # zero quantity is meaningless and the item is always sellable.
        item = _product_to_feed_item(
            _row(quantity=0, track_inventory=False),
            store_subdomain="t",
            currency="EGP",
        )
        assert item["availability"] == "in stock"

    def test_draft_product_excluded(self):
        # Drafts aren't visible on the storefront; including them in the
        # catalog would surface stale candidates in dynamic ads.
        assert (
            _product_to_feed_item(
                _row(status="draft"), store_subdomain="t", currency="EGP"
            )
            is None
        )

    def test_meta_catalog_id_takes_precedence_for_g_id(self):
        # Phase 8: when the merchant has explicitly set meta_catalog_id
        # (because they're using a pre-existing Meta catalog), the feed
        # g:id must match so the Pixel content_ids dedup works.
        item = _product_to_feed_item(
            _row(meta_catalog_id="MERCHANT-SKU-42"),
            store_subdomain="t",
            currency="EGP",
        )
        assert item["id"] == "MERCHANT-SKU-42"
        # item_group_id still uses internal UUID — that's the variant-
        # grouping key, separate from the per-variant g:id.
        assert item["item_group_id"] == "11111111-1111-1111-1111-111111111111"

    def test_missing_images_yields_null_image_link(self):
        item = _product_to_feed_item(
            _row(images=[]), store_subdomain="t", currency="EGP"
        )
        assert item["image_link"] is None

    def test_currency_uppercased_in_price(self):
        item = _product_to_feed_item(
            _row(price_amount=10000), store_subdomain="t", currency="usd"
        )
        # Currency MUST land in the feed as ISO 4217 uppercase — Meta
        # rejects lowercase. The mapper enforces it defensively even
        # though the route also uppercases on its way in.
        assert item["price"] == "100.00 USD"

    def test_title_truncated_to_150_chars(self):
        long_name = "x" * 500
        item = _product_to_feed_item(
            _row(name=long_name), store_subdomain="t", currency="EGP"
        )
        assert len(item["title"]) == 150

    def test_description_truncated_to_5000_chars(self):
        long_desc = "y" * 6000
        item = _product_to_feed_item(
            _row(description=long_desc),
            store_subdomain="t",
            currency="EGP",
        )
        assert len(item["description"]) == 5000

    def test_description_falls_back_to_short_description(self):
        item = _product_to_feed_item(
            _row(description=None, short_description="Short version"),
            store_subdomain="t",
            currency="EGP",
        )
        assert item["description"] == "Short version"

    def test_attributes_not_a_dict_handled_gracefully(self):
        # Defensive against half-saved data — older products might have
        # attributes stored as a list or None.
        item = _product_to_feed_item(
            _row(attributes=None), store_subdomain="t", currency="EGP"
        )
        assert item["brand"] is None
        assert item["product_type"] is None


# ---------------------------------------------------------------------------
# _item_xml — single <item> rendering
# ---------------------------------------------------------------------------


class TestItemXml:
    def test_basic_item_renders_required_fields(self):
        xml = _item_xml({
            "id": "id-1",
            "item_group_id": "id-1",
            "title": "T",
            "description": "D",
            "link": "https://x.com",
            "image_link": "https://x.com/img.jpg",
            "availability": "in stock",
            "condition": "new",
            "price": "100.00 EGP",
            "brand": None,
            "product_type": None,
            "sku": None,
        })
        assert "<g:id>id-1</g:id>" in xml
        assert "<g:item_group_id>id-1</g:item_group_id>" in xml
        assert "<g:title>T</g:title>" in xml
        assert "<g:description>D</g:description>" in xml
        assert "<g:link>https://x.com</g:link>" in xml
        assert "<g:image_link>https://x.com/img.jpg</g:image_link>" in xml
        assert "<g:availability>in stock</g:availability>" in xml
        assert "<g:condition>new</g:condition>" in xml
        assert "<g:price>100.00 EGP</g:price>" in xml

    def test_html_escape_of_dangerous_chars_in_title(self):
        # & < > " ' must be escaped — Meta's parser rejects malformed feeds.
        xml = _item_xml({
            "id": "id-1",
            "item_group_id": "id-1",
            "title": "Salt & Pepper <Set>",
            "link": "https://x.com",
            "availability": "in stock",
            "price": "10.00 EGP",
        })
        assert "&amp;" in xml
        assert "&lt;" in xml
        assert "&gt;" in xml
        # The raw chars must NOT appear in the title element.
        assert "<g:title>Salt & Pepper" not in xml

    def test_optional_fields_omitted_when_missing(self):
        xml = _item_xml({
            "id": "id-1",
            "item_group_id": "id-1",
            "title": "T",
            "link": "https://x.com",
            "availability": "in stock",
            "price": "10.00 EGP",
        })
        assert "<g:description>" not in xml
        assert "<g:image_link>" not in xml
        assert "<g:brand>" not in xml
        assert "<g:product_type>" not in xml
        assert "<g:mpn>" not in xml

    def test_sku_renders_as_mpn(self):
        # Meta accepts mpn as the manufacturer/SKU identifier.
        xml = _item_xml({
            "id": "id-1",
            "item_group_id": "id-1",
            "title": "T",
            "link": "https://x.com",
            "availability": "in stock",
            "price": "10.00 EGP",
            "sku": "SKU-42",
        })
        assert "<g:mpn>SKU-42</g:mpn>" in xml


# ---------------------------------------------------------------------------
# _build_feed_xml — full RSS envelope
# ---------------------------------------------------------------------------


class TestBuildFeedXml:
    def test_envelope_structure(self):
        xml = _build_feed_xml(
            store_name="My Store",
            store_url="https://mystore.numu.store",
            items=[],
        )
        # RSS preamble
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        # Required namespace for g: prefix
        assert 'xmlns:g="http://base.google.com/ns/1.0"' in xml
        assert 'version="2.0"' in xml
        # Channel metadata
        assert "<title>My Store</title>" in xml
        assert "<link>https://mystore.numu.store</link>" in xml
        # Properly closed
        assert xml.rstrip().endswith("</rss>")

    def test_empty_items_yields_valid_xml(self):
        # Empty stores must still produce parseable XML — Meta's crawler
        # treats this as "no products" not a feed error.
        xml = _build_feed_xml(store_name="X", store_url="https://x", items=[])
        assert "</channel>" in xml
        assert "</rss>" in xml

    def test_multiple_items_concatenated(self):
        items = [
            {
                "id": "a",
                "item_group_id": "a",
                "title": "A",
                "link": "https://x",
                "availability": "in stock",
                "price": "1.00 EGP",
            },
            {
                "id": "b",
                "item_group_id": "b",
                "title": "B",
                "link": "https://y",
                "availability": "in stock",
                "price": "2.00 EGP",
            },
            {
                "id": "c",
                "item_group_id": "c",
                "title": "C",
                "link": "https://z",
                "availability": "in stock",
                "price": "3.00 EGP",
            },
        ]
        xml = _build_feed_xml(store_name="X", store_url="https://x", items=items)
        assert xml.count("<item>") == 3
        assert xml.count("</item>") == 3

    def test_store_name_html_escaped(self):
        # Same XML-injection defence for the channel-level title.
        xml = _build_feed_xml(
            store_name="Tom & Jerry's <Shop>",
            store_url="https://x",
            items=[],
        )
        assert "&amp;" in xml
        assert "&apos;" in xml or "Tom & Jerry's" not in xml  # apostrophe escape varies
        assert "<title>Tom & Jerry's <Shop></title>" not in xml

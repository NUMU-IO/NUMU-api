"""Anonymous product-request fields must not become script for the merchant."""

import pytest

from src.api.v1.routes.storefront.product_requests import (
    _http_url,
    _merchant_email_html,
)


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://shop.example/p/1", "https://shop.example/p/1"),
        ("http://shop.example", "http://shop.example"),
        ("javascript:alert(document.cookie)", None),
        ("  JavaScript:alert(1)", None),
        ("data:text/html,<script>alert(1)</script>", None),
        ("", None),
        (None, None),
    ],
)
def test_source_url_keeps_only_http(url, kept):
    assert _http_url(url) == kept


def test_merchant_email_escapes_shopper_fields():
    html = _merchant_email_html(
        store_name="Shop",
        name="<img src=x onerror=alert(1)>",
        email='a@b.c"><script>x</script>',
        phone="<b>1</b>",
        details="<script>steal()</script>",
        images=[],
        source_url='https://x.test/"><script>y</script>',
    )

    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;steal()&lt;/script&gt;" in html

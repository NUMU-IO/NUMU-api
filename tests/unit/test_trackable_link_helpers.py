"""Unit tests for the helpers inside the trackable-link endpoint.

The full HTTP path is exercised via integration tests; this file
focuses on the rendering helper, which is pure-function and easy to
test in isolation.
"""

from __future__ import annotations

import base64

from src.api.v1.routes.stores.marketing_campaigns import _render_qr_png_base64


def test_render_qr_returns_base64_png():
    out = _render_qr_png_base64("https://acme.numueg.app/product/x")
    raw = base64.b64decode(out)
    # PNG magic number — confirms we actually rendered a valid image.
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")


def test_render_qr_is_deterministic_for_same_input():
    """Two consecutive renders of the same URL should produce the same
    bytes — important because the QR is the canonical machine-readable
    form of the trackable URL and merchants may compare/cache PNGs."""
    a = _render_qr_png_base64("https://acme.numueg.app/product/x")
    b = _render_qr_png_base64("https://acme.numueg.app/product/x")
    assert a == b


def test_render_qr_changes_with_input():
    a = _render_qr_png_base64("https://acme.numueg.app/product/x")
    b = _render_qr_png_base64("https://acme.numueg.app/product/y")
    assert a != b


def test_render_qr_handles_long_url():
    long_url = "https://acme.numueg.app/product/x?" + "&".join(
        f"k{i}=v{i}" for i in range(50)
    )
    out = _render_qr_png_base64(long_url)
    raw = base64.b64decode(out)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")

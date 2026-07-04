"""Unit tests for the theme-error beacon ingest — payload validation contract.

Pins the ingest schema: ``message`` is required + bounded, every other field
is optional but size-capped, and the server-side ``_cap`` truncation is a
no-op within the limit and a hard cut beyond it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.v1.routes.storefront.theme_error import (
    _BUNDLE_URL_MAX,
    _MESSAGE_MAX,
    _SLUG_MAX,
    _URL_MAX,
    _VERSION_MAX,
    ThemeErrorReportRequest,
    _cap,
)


class TestMessageField:
    def test_minimal_message_only_accepted(self):
        r = ThemeErrorReportRequest(message="TypeError: x is not a function")
        assert r.message == "TypeError: x is not a function"
        assert r.theme_slug is None
        assert r.theme_version is None
        assert r.bundle_url is None
        assert r.url is None

    def test_message_required(self):
        with pytest.raises(ValidationError):
            ThemeErrorReportRequest()  # type: ignore[call-arg]

    def test_empty_message_rejected(self):
        with pytest.raises(ValidationError):
            ThemeErrorReportRequest(message="")

    def test_oversize_message_rejected(self):
        with pytest.raises(ValidationError):
            ThemeErrorReportRequest(message="x" * (_MESSAGE_MAX + 1))

    def test_message_at_cap_accepted(self):
        r = ThemeErrorReportRequest(message="x" * _MESSAGE_MAX)
        assert len(r.message) == _MESSAGE_MAX


class TestOptionalFields:
    def test_full_payload_accepted(self):
        r = ThemeErrorReportRequest(
            message="boom",
            theme_slug="numu-theme-magic",
            theme_version="1.2.3",
            bundle_url="https://cdn.example/theme-v3/1.2.3/bundle.js",
            url="https://shop.numueg.app/product/abc",
        )
        assert r.theme_slug == "numu-theme-magic"
        assert r.theme_version == "1.2.3"

    def test_oversize_theme_slug_rejected(self):
        with pytest.raises(ValidationError):
            ThemeErrorReportRequest(message="boom", theme_slug="s" * (_SLUG_MAX + 1))

    def test_oversize_theme_version_rejected(self):
        with pytest.raises(ValidationError):
            ThemeErrorReportRequest(
                message="boom", theme_version="v" * (_VERSION_MAX + 1)
            )

    def test_oversize_bundle_url_rejected(self):
        with pytest.raises(ValidationError):
            ThemeErrorReportRequest(
                message="boom", bundle_url="u" * (_BUNDLE_URL_MAX + 1)
            )

    def test_oversize_url_rejected(self):
        with pytest.raises(ValidationError):
            ThemeErrorReportRequest(message="boom", url="u" * (_URL_MAX + 1))


class TestCapHelper:
    def test_none_passthrough(self):
        assert _cap(None, 10) is None

    def test_within_limit_unchanged(self):
        assert _cap("short", 10) == "short"

    def test_over_limit_truncated(self):
        assert _cap("x" * 20, 10) == "x" * 10

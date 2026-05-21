"""Unit tests for short_link_service pure functions.

The DB-dependent paths (create, resolve, bump) are exercised in the
integration suite; this file covers the security-critical pure-Python
logic that runs before any DB call:

* ``validate_destination_host`` — the open-redirector defence
* ``_random_code`` — generator alphabet + length
* ``_normalize_host`` — case + www handling
* ``_origin_host`` — URL parsing
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.services.short_link_service import (
    _ALPHABET,
    _CODE_LENGTH,
    OpenRedirectorError,
    _normalize_host,
    _origin_host,
    _random_code,
    validate_destination_host,
)


class _FakeStore:
    """Minimal Store-shaped stub for host validation tests.

    The validator only reaches for ``.store_url``, ``.subdomain``, and
    ``.id`` — building a full Store entity (which pulls owner_id, tenant
    relationships, etc.) for each test would be noise.
    """

    def __init__(
        self,
        *,
        subdomain: str | None = None,
        custom_domain: str | None = None,
        slug: str = "acme",
    ):
        self.id = uuid4()
        self.subdomain = subdomain
        self.custom_domain = custom_domain
        self.slug = slug

    @property
    def store_url(self) -> str:
        if self.custom_domain:
            return f"https://{self.custom_domain}"
        if self.subdomain:
            return f"https://{self.subdomain}.numueg.app"
        return f"https://{self.slug}.numueg.app"


class TestRandomCode:
    def test_correct_length(self):
        code = _random_code()
        assert len(code) == _CODE_LENGTH == 8

    def test_uses_only_crockford_alphabet(self):
        # Every char must be from the Crockford set — no I/L/O/U so
        # printed/QR-scanned codes don't get misread.
        for _ in range(100):
            code = _random_code()
            for ch in code:
                assert ch in _ALPHABET

    def test_no_forbidden_chars(self):
        forbidden = {"I", "L", "O", "U"}
        for _ in range(200):
            code = _random_code()
            assert not (set(code) & forbidden), f"{code!r} has forbidden chars"

    def test_codes_vary(self):
        # Not a randomness test per se — just confirm we're not
        # returning a constant. 32^8 means 100 draws should all differ.
        codes = {_random_code() for _ in range(100)}
        assert len(codes) == 100


class TestNormalizeHost:
    def test_lowercases(self):
        assert _normalize_host("ACME.numueg.app") == "acme.numueg.app"

    def test_strips_www(self):
        assert _normalize_host("www.acme.com") == "acme.com"

    def test_strips_www_after_lowercase(self):
        assert _normalize_host("WWW.acme.com") == "acme.com"

    def test_trims_whitespace(self):
        assert _normalize_host("  acme.com  ") == "acme.com"

    def test_empty_string(self):
        assert _normalize_host("") == ""

    def test_none(self):
        assert _normalize_host(None) == ""

    def test_does_not_strip_inner_www(self):
        # "wwwfoo.com" shouldn't lose its prefix
        assert _normalize_host("wwwfoo.com") == "wwwfoo.com"


class TestOriginHost:
    def test_https_origin(self):
        assert _origin_host("https://acme.numueg.app") == "acme.numueg.app"

    def test_http_origin(self):
        assert _origin_host("http://acme.numueg.app") == "acme.numueg.app"

    def test_with_trailing_slash(self):
        assert _origin_host("https://acme.numueg.app/") == "acme.numueg.app"

    def test_custom_domain(self):
        assert _origin_host("https://shop.acme.com") == "shop.acme.com"

    def test_lowercases(self):
        assert _origin_host("https://ACME.NUMUEG.APP") == "acme.numueg.app"


class TestValidateDestinationHost:
    """SEC: the open-redirector defence.

    Without this check a merchant (or anyone who reached the
    trackable-link endpoint) could mint short codes that point at
    phishing URLs and ride numueg.app's reputation. Every accept and
    every reject case here is a security boundary.
    """

    def test_accepts_subdomain_origin(self):
        store = _FakeStore(subdomain="acme")
        validate_destination_host(
            "https://acme.numueg.app/product/foo?utm_source=facebook",
            store,
        )

    def test_accepts_custom_domain_origin(self):
        store = _FakeStore(subdomain="acme", custom_domain="shop.acme.com")
        validate_destination_host(
            "https://shop.acme.com/product/foo?utm_source=facebook",
            store,
        )

    def test_accepts_subdomain_fallback_when_custom_domain_set(self):
        # Custom domain wins for canonical origin, but the subdomain
        # is still valid — useful for links composed before a custom
        # domain was added.
        store = _FakeStore(subdomain="acme", custom_domain="shop.acme.com")
        validate_destination_host(
            "https://acme.numueg.app/product/foo",
            store,
        )

    def test_case_insensitive(self):
        store = _FakeStore(subdomain="acme")
        validate_destination_host(
            "https://ACME.NUMUEG.APP/product/foo",
            store,
        )

    def test_rejects_arbitrary_external_url(self):
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError, match="does not belong to store"):
            validate_destination_host("https://attacker.com/phish", store)

    def test_rejects_other_numueg_store_subdomain(self):
        # acme cannot mint a short_link that points to victim.numueg.app
        # — that would let a phishing campaign hide behind another
        # merchant's brand.
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError):
            validate_destination_host("https://victim.numueg.app/checkout", store)

    def test_rejects_javascript_scheme(self):
        # The big one: ``javascript:alert(1)`` would XSS anyone clicking
        # the short link if a 302 ever forwarded it.
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError, match="scheme"):
            validate_destination_host("javascript:alert(1)", store)

    def test_rejects_data_scheme(self):
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError, match="scheme"):
            validate_destination_host(
                "data:text/html,<script>alert(1)</script>",
                store,
            )

    def test_rejects_file_scheme(self):
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError, match="scheme"):
            validate_destination_host("file:///etc/passwd", store)

    def test_rejects_empty_url(self):
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError, match="destination_url is required"):
            validate_destination_host("", store)

    def test_rejects_url_without_host(self):
        # A scheme-only URL slips past the scheme check but has no
        # netloc — must still reject.
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError, match="has no host"):
            validate_destination_host("https://", store)

    def test_strips_www_on_destination(self):
        # If the merchant typed www.acme.numueg.app it should still
        # match the canonical acme.numueg.app — DNS treats them as the
        # same host in practice.
        store = _FakeStore(subdomain="acme")
        validate_destination_host(
            "https://www.acme.numueg.app/product/foo",
            store,
        )

    def test_rejects_lookalike_subdomain(self):
        # acme-evil.numueg.app must NOT pass even though it starts
        # with "acme" — substring sneaking is exactly what this check
        # exists to block.
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError):
            validate_destination_host(
                "https://acme-evil.numueg.app/page",
                store,
            )

    def test_rejects_subdomain_of_store_subdomain(self):
        # foo.acme.numueg.app is not the same as acme.numueg.app.
        # Reject — we don't run wildcards in the validator.
        store = _FakeStore(subdomain="acme")
        with pytest.raises(OpenRedirectorError):
            validate_destination_host(
                "https://foo.acme.numueg.app/page",
                store,
            )

    def test_accepts_url_with_path_and_query(self):
        # Real trackable URLs carry UTMs; make sure the validator
        # only cares about the host, not the rest.
        store = _FakeStore(subdomain="acme")
        validate_destination_host(
            "https://acme.numueg.app/product/foo?utm_source=facebook"
            "&utm_medium=social&utm_campaign=eid-sale-AB7K9X",
            store,
        )

    def test_store_with_only_slug_uses_slug_subdomain(self):
        # When subdomain isn't set (rare; legacy stores), Store.store_url
        # falls back to `<slug>.numueg.app`. The validator should match
        # against that.
        store = _FakeStore(subdomain=None, slug="legacy")
        validate_destination_host(
            "https://legacy.numueg.app/product/foo",
            store,
        )

"""Google Maps browser-key referrer allowlist maintenance."""

from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.infrastructure.external_services.google_maps_key_service import (
    GoogleMapsKeyError,
    GoogleMapsKeyService,
    referrer_patterns,
)


@pytest.fixture
def configured(monkeypatch):
    """Settings that make the service live, so is_enabled is exercised too."""
    monkeypatch.setattr(settings, "google_maps_key_project", "numu-prod", False)
    monkeypatch.setattr(settings, "google_maps_key_id", "key-abc", False)
    monkeypatch.setattr(
        settings, "google_maps_key_credentials", '{"type":"service_account"}', False
    )
    return GoogleMapsKeyService()


class _Resp:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class TestReferrerPatterns:
    def test_covers_apex_and_subdomains(self):
        # https://brand.com/* does NOT match www.brand.com — the wildcard
        # entry is what keeps `www` working, so both must be emitted.
        assert referrer_patterns("brand.com") == [
            "https://brand.com/*",
            "https://*.brand.com/*",
        ]

    @pytest.mark.parametrize(
        "raw",
        ["BRAND.com", "https://brand.com", "http://brand.com/checkout", " brand.com. "],
    )
    def test_normalises_scheme_case_path_and_trailing_dot(self, raw):
        assert referrer_patterns(raw) == [
            "https://brand.com/*",
            "https://*.brand.com/*",
        ]

    def test_empty_domain_yields_nothing(self):
        assert referrer_patterns("") == []
        assert referrer_patterns("   ") == []


class TestAuthorizeDomain:
    @pytest.mark.asyncio
    async def test_disabled_service_is_inert(self):
        svc = GoogleMapsKeyService()  # no settings configured
        with patch.object(svc, "_request", new=AsyncMock()) as req:
            assert await svc.authorize_domain("brand.com") is False
        req.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_appends_without_dropping_existing_referrers(self, configured):
        svc = configured
        existing = ["https://numueg.app/*", "https://*.numueg.app/*"]
        calls = []

        async def fake(method, path, *, params=None, json_body=None):
            calls.append((method, json_body))
            if method == "GET":
                return _Resp(
                    200,
                    {
                        "etag": "e1",
                        "restrictions": {
                            "browserKeyRestrictions": {"allowedReferrers": existing}
                        },
                    },
                )
            return _Resp(200, {})

        with patch.object(svc, "_request", new=AsyncMock(side_effect=fake)):
            assert await svc.authorize_domain("brand.com") is True

        patch_body = next(b for m, b in calls if m == "PATCH")
        sent = patch_body["restrictions"]["browserKeyRestrictions"]["allowedReferrers"]
        # The platform's own hosts must survive: this is a read-modify-write
        # on one shared key, and clobbering them would break every storefront.
        assert sent == existing + [
            "https://brand.com/*",
            "https://*.brand.com/*",
        ]
        assert patch_body["etag"] == "e1"

    @pytest.mark.asyncio
    async def test_already_authorised_does_not_write(self, configured):
        svc = configured
        current = ["https://brand.com/*", "https://*.brand.com/*"]

        async def fake(method, path, *, params=None, json_body=None):
            assert method == "GET", "an already-authorised domain must not PATCH"
            return _Resp(
                200,
                {
                    "etag": "e1",
                    "restrictions": {
                        "browserKeyRestrictions": {"allowedReferrers": current}
                    },
                },
            )

        with patch.object(svc, "_request", new=AsyncMock(side_effect=fake)):
            assert await svc.authorize_domain("brand.com") is False

    @pytest.mark.asyncio
    async def test_retries_when_the_key_changed_under_us(self, configured):
        """A concurrent activation invalidates our etag; retry, don't drop it."""
        svc = configured
        attempts = {"patch": 0}

        async def fake(method, path, *, params=None, json_body=None):
            if method == "GET":
                return _Resp(
                    200,
                    {
                        "etag": "e1",
                        "restrictions": {
                            "browserKeyRestrictions": {"allowedReferrers": []}
                        },
                    },
                )
            attempts["patch"] += 1
            return _Resp(200 if attempts["patch"] > 1 else 409, {})

        with patch.object(svc, "_request", new=AsyncMock(side_effect=fake)):
            assert await svc.authorize_domain("brand.com") is True
        assert attempts["patch"] == 2

    @pytest.mark.asyncio
    async def test_gives_up_after_repeated_conflicts(self, configured):
        svc = configured

        async def fake(method, path, *, params=None, json_body=None):
            if method == "GET":
                return _Resp(
                    200,
                    {
                        "etag": "e1",
                        "restrictions": {
                            "browserKeyRestrictions": {"allowedReferrers": []}
                        },
                    },
                )
            return _Resp(409, {})

        with patch.object(svc, "_request", new=AsyncMock(side_effect=fake)):
            with pytest.raises(GoogleMapsKeyError):
                await svc.authorize_domain("brand.com")

    @pytest.mark.asyncio
    async def test_read_failure_raises(self, configured):
        svc = configured
        with patch.object(
            svc, "_request", new=AsyncMock(return_value=_Resp(403, {"e": "denied"}))
        ):
            with pytest.raises(GoogleMapsKeyError):
                await svc.authorize_domain("brand.com")


class TestRevokeDomain:
    @pytest.mark.asyncio
    async def test_removes_only_that_domain(self, configured):
        svc = configured
        current = [
            "https://numueg.app/*",
            "https://brand.com/*",
            "https://*.brand.com/*",
        ]
        sent: dict = {}

        async def fake(method, path, *, params=None, json_body=None):
            if method == "GET":
                return _Resp(
                    200,
                    {
                        "etag": "e1",
                        "restrictions": {
                            "browserKeyRestrictions": {"allowedReferrers": current}
                        },
                    },
                )
            sent.update(json_body)
            return _Resp(200, {})

        with patch.object(svc, "_request", new=AsyncMock(side_effect=fake)):
            assert await svc.revoke_domain("brand.com") is True

        assert sent["restrictions"]["browserKeyRestrictions"]["allowedReferrers"] == [
            "https://numueg.app/*"
        ]

    @pytest.mark.asyncio
    async def test_never_writes_an_empty_allowlist(self, configured):
        """An unrestricted browser key is billable by anyone who copies it."""
        svc = configured

        async def fake(method, path, *, params=None, json_body=None):
            assert method == "GET", "must not PATCH the key to an empty allowlist"
            return _Resp(
                200,
                {
                    "etag": "e1",
                    "restrictions": {
                        "browserKeyRestrictions": {
                            "allowedReferrers": [
                                "https://brand.com/*",
                                "https://*.brand.com/*",
                            ]
                        }
                    },
                },
            )

        with patch.object(svc, "_request", new=AsyncMock(side_effect=fake)):
            assert await svc.revoke_domain("brand.com") is False

    @pytest.mark.asyncio
    async def test_unknown_domain_is_a_noop(self, configured):
        svc = configured

        async def fake(method, path, *, params=None, json_body=None):
            assert method == "GET"
            return _Resp(
                200,
                {
                    "etag": "e1",
                    "restrictions": {
                        "browserKeyRestrictions": {
                            "allowedReferrers": ["https://numueg.app/*"]
                        }
                    },
                },
            )

        with patch.object(svc, "_request", new=AsyncMock(side_effect=fake)):
            assert await svc.revoke_domain("other.com") is False

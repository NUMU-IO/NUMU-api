"""Tests for the registry-driven carrier management endpoints (P3).

The bug these exist for: ``save_bosta_credentials`` set
``is_configured: True`` without ever calling Bosta, so a typo'd API key
showed a green "Live" badge. The hub patched around it with a
``localStorage`` probe — per browser, lost on clear, invisible to
support. Verification is now server-side and persisted.
"""

import pytest

from src.api.v1.routes.stores.carriers import _carrier_status, _run_verification
from src.application.services.carrier_registry import get_spec


class TestVerificationHonesty:
    """A badge the server cannot justify must not be shown."""

    @pytest.mark.asyncio
    async def test_carrier_without_a_probe_reports_unknown_not_false(self):
        """Mylerz and J&T have no safe read-only call.

        `None` means "we can't tell", which the UI renders as
        "configured, not verified". Returning False would read as
        "broken", and True would be the original lie.
        """
        for slug in ("mylerz", "jt"):
            assert get_spec(slug).verification_operation is None
            verified, error = await _run_verification(slug, {})
            assert verified is None
            assert error is None

    def test_bosta_declares_a_probe(self):
        assert get_spec("bosta").verification_operation == "get_cities"

    @pytest.mark.asyncio
    async def test_a_failing_probe_is_false_with_a_reason(self, monkeypatch):
        """A wrong key must read as unverified, and say why.

        The reason matters: support needs to tell a bad key from a Bosta
        outage.
        """

        async def _boom(*args, **kwargs):
            raise RuntimeError("401 Unauthorized")

        import src.api.v1.routes.stores.carriers as mod

        class _Svc:
            get_cities = staticmethod(_boom)

        async def _factory(slug, settings):
            return _Svc()

        monkeypatch.setattr(mod, "service_for_carrier", _factory)
        verified, error = await _run_verification("bosta", {})
        assert verified is False
        assert "401" in error

    @pytest.mark.asyncio
    async def test_a_passing_probe_is_true(self, monkeypatch):
        import src.api.v1.routes.stores.carriers as mod

        class _Svc:
            @staticmethod
            async def get_cities():
                return [{"id": "1"}]

        async def _factory(slug, settings):
            return _Svc()

        monkeypatch.setattr(mod, "service_for_carrier", _factory)
        assert await _run_verification("bosta", {}) == (True, None)

    @pytest.mark.asyncio
    async def test_error_message_is_truncated(self, monkeypatch):
        """Carrier errors can be whole HTML pages; don't store one."""

        import src.api.v1.routes.stores.carriers as mod

        class _Svc:
            @staticmethod
            async def get_cities():
                raise RuntimeError("x" * 5000)

        async def _factory(slug, settings):
            return _Svc()

        monkeypatch.setattr(mod, "service_for_carrier", _factory)
        _, error = await _run_verification("bosta", {})
        assert len(error) <= 500


class TestCarrierStatus:
    """Status is derived from stored state and leaks no secrets."""

    class _Store:
        def __init__(self, settings):
            self.settings = settings

    def test_unconfigured_store_reports_cleanly(self):
        status = _carrier_status(self._Store({}), "bosta")
        assert status["is_configured"] is False
        assert status["enabled"] is False
        assert status["verified"] is None

    def test_requires_both_ciphertext_and_key_id(self):
        """Half-stored credentials are unusable — not "configured"."""
        half = self._Store({"shipping": {"bosta": {"encrypted_credentials": "abc"}}})
        assert _carrier_status(half, "bosta")["is_configured"] is False

        whole = self._Store({
            "shipping": {
                "bosta": {
                    "encrypted_credentials": "abc",
                    "encryption_key_id": "k1",
                }
            }
        })
        assert _carrier_status(whole, "bosta")["is_configured"] is True

    def test_never_returns_credential_values(self):
        store = self._Store({
            "shipping": {
                "bosta": {
                    "encrypted_credentials": "SECRET-CIPHERTEXT",
                    "encryption_key_id": "k1",
                    "api_key": "SECRET-PLAINTEXT",
                }
            }
        })
        blob = repr(_carrier_status(store, "bosta"))
        assert "SECRET-CIPHERTEXT" not in blob
        assert "SECRET-PLAINTEXT" not in blob

    def test_surfaces_verification_state(self):
        store = self._Store({
            "shipping": {
                "bosta": {
                    "encrypted_credentials": "a",
                    "encryption_key_id": "k",
                    "verified": False,
                    "verification_error": "401 Unauthorized",
                }
            }
        })
        status = _carrier_status(store, "bosta")
        assert status["verified"] is False
        assert status["verification_error"] == "401 Unauthorized"


class TestCatalogShape:
    """The hub renders entirely from this payload."""

    def test_can_verify_reflects_the_registry(self):
        from src.application.services.carrier_resolver import carrier_catalog

        by_slug = {c["slug"]: c for c in carrier_catalog()}
        assert by_slug["bosta"]["can_verify"] is True
        assert by_slug["mylerz"]["can_verify"] is False

    def test_entries_carry_everything_the_hub_needs(self):
        from src.application.services.carrier_resolver import carrier_catalog

        for entry in carrier_catalog():
            assert entry["name_en"] and entry["name_ar"]
            assert entry["brand_color"], f"{entry['slug']} has no brand colour"
            assert isinstance(entry["capabilities"], dict)
            assert isinstance(entry["credential_fields"], list)
            for field in entry["credential_fields"]:
                assert field["label_en"] and field["label_ar"]

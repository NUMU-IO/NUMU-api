"""Tests for the carrier registry (P1.3).

The registry replaces five hardcoded carrier surfaces that had drifted
apart. The most valuable test here is
``test_declared_capabilities_match_the_provider`` — capability is now
*declared* rather than introspected, and a declaration that lies is
worse than no declaration: the hub would offer a button that 501s, or
hide one that works.
"""

import pytest

from src.application.services.carrier_registry import (
    CARRIERS,
    CredentialField,
    all_carriers,
    carrier_slugs,
    catalog,
    default_carrier,
    get_spec,
    validate_registry,
)
from src.application.services.carrier_resolver import (
    OPERATION_CAPABILITY,
    supported_operations,
    supports,
)
from src.core.entities.shipment import ShipmentStatus

# Provider method that backs each declared capability, for the
# declaration-vs-reality check.
CAPABILITY_METHODS: dict[str, tuple[str, ...]] = {
    "supports_cancel": ("cancel_shipment",),
    "supports_return": ("request_return",),
    "supports_labels": ("print_awb", "get_label"),
    "supports_pickup": ("create_pickup",),
    "supports_city_lookup": ("get_cities",),
    "supports_delivery_update": ("update_delivery",),
    "supports_live_rates": ("get_rates",),
    "supports_tracking": ("track_shipment",),
}


class TestRegistryIntegrity:
    def test_validate_registry_passes(self):
        validate_registry()  # also runs at import; explicit here

    def test_exactly_one_default(self):
        assert [s.slug for s in all_carriers() if s.is_default] == [default_carrier()]

    def test_keys_match_slugs(self):
        for key, spec in CARRIERS.items():
            assert key == spec.slug

    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_every_carrier_is_bilingual(self, slug):
        spec = get_spec(slug)
        assert spec.name_en
        assert spec.name_ar
        assert any("؀" <= ch <= "ۿ" for ch in spec.name_ar), (
            f"{slug} name_ar is not Arabic"
        )

    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_credential_labels_are_bilingual(self, slug):
        for cred in get_spec(slug).credential_fields:
            assert cred.label_en and cred.label_ar
            assert any("؀" <= ch <= "ۿ" for ch in cred.label_ar)

    def test_jt_is_registered(self):
        """J&T is creatable via shipments but absent from the settings
        route's hardcoded tuple. The registry is the correct side; P1.6
        migrates settings onto it. Don't resolve the drift by dropping J&T.
        """
        assert get_spec("jt") is not None


class TestCapabilityTruthfulness:
    """A declared capability must match what the provider can really do."""

    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_declared_capabilities_match_the_provider(self, slug):
        spec = get_spec(slug)
        cls = spec.provider_class()
        for capability_name, methods in CAPABILITY_METHODS.items():
            declared = getattr(spec.capabilities, capability_name)
            implemented = any(callable(getattr(cls, m, None)) for m in methods)
            if declared:
                assert implemented, (
                    f"{slug} declares {capability_name} but {cls.__name__} "
                    f"implements none of {methods} — the hub would offer a "
                    f"button that 501s"
                )

    def test_bosta_is_the_full_featured_one(self):
        caps = get_spec("bosta").capabilities
        for name in (
            "supports_cod",
            "supports_labels",
            "supports_pickup",
            "supports_return",
            "supports_cancel",
            "supports_webhooks",
            "supports_city_lookup",
        ):
            assert getattr(caps, name), name

    @pytest.mark.parametrize("slug", ["mylerz", "jt"])
    def test_thin_carriers_declare_narrowly(self, slug):
        """They implement 4 of Bosta's 20 methods — must not claim more.

        P4 widens these once implemented and verified against the live API.
        """
        caps = get_spec(slug).capabilities
        for name in (
            "supports_labels",
            "supports_pickup",
            "supports_cancel",
            "supports_return",
            "supports_city_lookup",
            "supports_delivery_update",
            "supports_live_rates",
        ):
            assert not getattr(caps, name), f"{slug} should not claim {name} yet"

    def test_capabilities_default_to_false(self):
        """Forgetting to declare must fail closed, not open."""
        from src.core.interfaces.services.shipping_provider import (
            ProviderCapabilities,
        )

        caps = ProviderCapabilities()
        assert not any(caps.as_dict().values())


class TestStatusMaps:
    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_keys_are_uppercase(self, slug):
        """map_status() uppercases before lookup, so a lowercase key is dead."""
        for raw in get_spec(slug).status_map:
            assert raw == raw.upper()

    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_webhook_carriers_can_map_a_terminal_status(self, slug):
        spec = get_spec(slug)
        if not spec.capabilities.supports_webhooks:
            pytest.skip("no webhooks")
        mapped = set(spec.status_map.values())
        assert ShipmentStatus.DELIVERED in mapped
        assert mapped & {ShipmentStatus.RETURNED, ShipmentStatus.CANCELLED}

    def test_unknown_status_returns_none_not_a_guess(self):
        assert get_spec("bosta").map_status("SOMETHING_NEW") is None
        assert get_spec("bosta").map_status(None) is None

    def test_mapping_is_case_and_space_tolerant(self):
        spec = get_spec("bosta")
        assert spec.map_status("  delivered ") is ShipmentStatus.DELIVERED


class TestTrackingUrls:
    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_template_interpolates_and_is_https(self, slug):
        spec = get_spec(slug)
        if not spec.tracking_url_template:
            pytest.skip("no tracking page")
        url = spec.tracking_url("ABC123")
        assert "ABC123" in url
        assert url.startswith("https://")

    def test_no_carrier_borrows_another_carriers_url(self):
        for spec in all_carriers():
            url = spec.tracking_url("ABC123") or ""
            for other in all_carriers():
                if other.slug == spec.slug or not other.tracking_url_template:
                    continue
                host = other.tracking_url_template.split("/")[2]
                assert host not in url, f"{spec.slug} points at {other.slug}'s host"

    def test_missing_tracking_number_yields_none(self):
        assert get_spec("bosta").tracking_url(None) is None
        assert get_spec("bosta").tracking_url("") is None


class TestCatalog:
    def test_lists_selectable_carriers(self):
        assert {c["slug"] for c in catalog()} == set(
            carrier_slugs(selectable_only=True)
        )

    def test_entries_carry_what_the_hub_needs(self):
        for entry in catalog():
            assert entry["name_en"] and entry["name_ar"]
            assert isinstance(entry["capabilities"], dict)
            assert isinstance(entry["credential_fields"], list)
            assert entry["tier"] in ("native", "aggregator", "manual")

    def test_credentials_can_be_withheld(self):
        for entry in catalog(include_credentials=False):
            assert "credential_fields" not in entry

    def test_catalog_needs_no_credentials_or_network(self):
        """The hub must be able to render settings for a store that has
        connected nothing at all."""
        assert catalog()


class TestOperationCapabilityMap:
    def test_every_mapped_capability_exists(self):
        from src.core.interfaces.services.shipping_provider import (
            ProviderCapabilities,
        )

        valid = set(ProviderCapabilities().as_dict())
        for operation, capability_name in OPERATION_CAPABILITY.items():
            assert capability_name in valid, (
                f"{operation} maps to unknown capability {capability_name}"
            )

    def test_supports_is_false_for_unknown_carrier(self):
        assert not supports("aramex", "cancel_shipment")
        assert supported_operations("aramex") == []

    def test_bosta_supports_more_than_the_thin_carriers(self):
        assert len(supported_operations("bosta")) > len(supported_operations("mylerz"))


class TestCredentialField:
    def test_secret_by_default(self):
        assert CredentialField(key="k", label_en="K", label_ar="ك").secret

    def test_serialises_for_the_hub(self):
        d = CredentialField(
            key="api_key", label_en="API key", label_ar="مفتاح"
        ).as_dict()
        assert d["key"] == "api_key"
        assert set(d) >= {"key", "label_en", "label_ar", "required", "secret"}

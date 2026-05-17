"""Wave 4 Phase 25 — B2B pixel-exclusion filter tests.

The filter is pure-Python predicate logic — easy to test without DB
or external services. Pins:
  * ``is_b2b_enabled`` returns false when feature flag is missing
  * Role-based exclusion catches builtins + merchant-configured roles
  * Product-based exclusion catches the four marker patterns
  * Non-B2B stores see zero filtering (zero behavior change)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.application.services.meta_b2b_pixel_filter import (
    is_b2b_enabled,
    should_skip_for_b2b_product,
    should_skip_for_b2b_role,
)


class TestIsB2BEnabled:
    def test_none_settings_disabled(self):
        assert is_b2b_enabled(None) is False

    def test_empty_settings_disabled(self):
        assert is_b2b_enabled({}) is False

    def test_b2b_section_missing_disabled(self):
        assert is_b2b_enabled({"other": "stuff"}) is False

    def test_b2b_enabled_false_disabled(self):
        assert is_b2b_enabled({"b2b": {"enabled": False}}) is False

    def test_b2b_enabled_true_enabled(self):
        assert is_b2b_enabled({"b2b": {"enabled": True}}) is True


class TestShouldSkipForB2BRole:
    """Non-B2B stores see zero filtering; B2B stores skip wholesale roles."""

    @pytest.fixture
    def b2b_settings(self) -> dict:
        return {"b2b": {"enabled": True}}

    def test_non_b2b_store_never_skips(self):
        # Even a customer with role=wholesale_buyer fires Pixel when
        # the store hasn't enabled B2B — defensive against orphaned data.
        customer = SimpleNamespace(role="wholesale_buyer")
        assert should_skip_for_b2b_role({}, customer) is False

    def test_b2b_store_no_customer_no_skip(self):
        # Anonymous visitors (cart-abandon flow, browse without login)
        # don't have a role; can't classify them as B2B.
        assert should_skip_for_b2b_role({"b2b": {"enabled": True}}, None) is False

    @pytest.mark.parametrize(
        "role", ["wholesale_buyer", "WHOLESALE_BUYER", "b2b_buyer", "wholesale"]
    )
    def test_builtin_b2b_roles_skip(self, b2b_settings, role):
        customer = SimpleNamespace(role=role)
        assert should_skip_for_b2b_role(b2b_settings, customer) is True

    def test_unknown_role_does_not_skip(self, b2b_settings):
        customer = SimpleNamespace(role="customer")  # retail
        assert should_skip_for_b2b_role(b2b_settings, customer) is False

    def test_merchant_extra_excluded_roles(self):
        settings = {
            "b2b": {"enabled": True, "excluded_roles": ["distributor", "reseller"]}
        }
        assert (
            should_skip_for_b2b_role(settings, SimpleNamespace(role="distributor"))
            is True
        )
        assert (
            should_skip_for_b2b_role(settings, SimpleNamespace(role="reseller")) is True
        )
        # Retail role still fires.
        assert (
            should_skip_for_b2b_role(settings, SimpleNamespace(role="customer"))
            is False
        )

    def test_customer_as_dict_supported(self, b2b_settings):
        # API responses sometimes pass customer as a dict not an object.
        assert (
            should_skip_for_b2b_role(b2b_settings, {"role": "wholesale_buyer"}) is True
        )


class TestShouldSkipForB2BProduct:
    """Filters products marked B2B-only via any of 4 marker patterns."""

    @pytest.fixture
    def b2b_settings(self) -> dict:
        return {"b2b": {"enabled": True}}

    def test_non_b2b_store_never_skips(self):
        product = SimpleNamespace(b2b_only=True)
        assert should_skip_for_b2b_product({}, product) is False

    def test_none_product_no_skip(self, b2b_settings):
        assert should_skip_for_b2b_product(b2b_settings, None) is False

    def test_b2b_only_attribute_on_entity(self, b2b_settings):
        product = SimpleNamespace(b2b_only=True)
        assert should_skip_for_b2b_product(b2b_settings, product) is True

    def test_b2b_tag_in_tags(self, b2b_settings):
        product = SimpleNamespace(tags=["b2b", "summer-collection"])
        assert should_skip_for_b2b_product(b2b_settings, product) is True

    def test_wholesale_tag_in_tags(self, b2b_settings):
        product = SimpleNamespace(tags=["wholesale"])
        assert should_skip_for_b2b_product(b2b_settings, product) is True

    def test_b2b_only_tag_with_underscore(self, b2b_settings):
        product = SimpleNamespace(tags=["b2b_only"])
        assert should_skip_for_b2b_product(b2b_settings, product) is True

    def test_tags_case_insensitive(self, b2b_settings):
        product = SimpleNamespace(tags=["B2B", "Special"])
        assert should_skip_for_b2b_product(b2b_settings, product) is True

    def test_attributes_b2b_only_true(self, b2b_settings):
        product = SimpleNamespace(attributes={"b2b_only": True, "color": "red"})
        assert should_skip_for_b2b_product(b2b_settings, product) is True

    def test_attributes_b2b_only_false_does_not_skip(self, b2b_settings):
        product = SimpleNamespace(attributes={"b2b_only": False})
        assert should_skip_for_b2b_product(b2b_settings, product) is False

    def test_regular_product_does_not_skip(self, b2b_settings):
        product = SimpleNamespace(tags=["summer", "limited-edition"])
        assert should_skip_for_b2b_product(b2b_settings, product) is False

    def test_product_as_dict_supported(self, b2b_settings):
        # API responses sometimes pass product as a dict.
        assert should_skip_for_b2b_product(b2b_settings, {"tags": ["b2b"]}) is True
        assert (
            should_skip_for_b2b_product(
                b2b_settings, {"attributes": {"b2b_only": True}}
            )
            is True
        )

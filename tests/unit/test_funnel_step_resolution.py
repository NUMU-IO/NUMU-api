"""Unit tests for /track funnel-step resolution + navigation gating.

Regression guard for the analytics-truth fix: historically only 6 steps
were honored and everything else (search, add_payment_info, …) was
silently rewritten to page_view — losing the step, polluting the
page_views table, and breaking Meta event_id dedup (the browser had
fired the real event name with the same event_id).
"""

import pytest

from src.api.v1.routes.storefront.tracking import (
    _NAVIGATION_STEPS,
    _VALID_FUNNEL_STEPS,
    resolve_funnel_step,
)


class TestResolveFunnelStep:
    @pytest.mark.parametrize(
        "step",
        [
            "page_view",
            "product_view",
            "collection_view",
            "add_to_cart",
            "checkout_started",
            "add_shipping_info",
            "add_payment_info",
            "order_completed",
            "order_delivered",
            "search",
            "lead",
            "sign_up",
            "complete_registration",
            "subscribe",
            "contact",
            "add_to_wishlist",
            "customize_product",
            "view_cart",
            "remove_from_cart",
        ],
    )
    def test_valid_explicit_steps_pass_through(self, step):
        assert resolve_funnel_step(step, "/whatever") == step

    def test_search_is_no_longer_collapsed_to_page_view(self):
        # The original bug: step="search" was rewritten to page_view.
        assert resolve_funnel_step("search", "/search?q=shoes") == "search"

    def test_add_payment_info_is_no_longer_collapsed(self):
        assert (
            resolve_funnel_step("add_payment_info", "/checkout/payment")
            == "add_payment_info"
        )

    def test_unknown_step_falls_back_to_path_inference(self):
        # Unauthenticated endpoint — arbitrary client strings must not
        # become funnel rows.
        assert resolve_funnel_step("evil_step'; DROP", "/about") == "page_view"
        assert resolve_funnel_step("custom_thing", "/product/abc") == "product_view"

    def test_product_detail_path_infers_product_view(self):
        assert resolve_funnel_step(None, "/product/some-slug") == "product_view"

    def test_products_listing_path_infers_page_view(self):
        assert resolve_funnel_step(None, "/products") == "page_view"
        assert resolve_funnel_step(None, "/products?page=2") == "page_view"

    def test_missing_step_and_path_defaults_to_page_view(self):
        assert resolve_funnel_step(None, None) == "page_view"
        assert resolve_funnel_step("", "") == "page_view"


class TestNavigationGating:
    """Only navigation steps may create page_views rows — sessions,
    bounce rate, and landing pages are derived from that table."""

    def test_navigation_steps_are_exactly_the_page_load_steps(self):
        assert _NAVIGATION_STEPS == {"page_view", "product_view", "collection_view"}

    @pytest.mark.parametrize(
        "step",
        ["search", "add_payment_info", "add_to_cart", "checkout_started", "lead"],
    )
    def test_pure_funnel_steps_are_not_navigation(self, step):
        assert step in _VALID_FUNNEL_STEPS
        assert step not in _NAVIGATION_STEPS

    def test_navigation_steps_are_all_valid(self):
        assert _NAVIGATION_STEPS <= _VALID_FUNNEL_STEPS

"""Unit tests for the Meta tracking settings request schemas + helpers.

Covers the validation contract surfaced by Wave 1C:

  * pixel_id regex (15-16 digits)
  * test_event_code regex (^TEST\\d+$)
  * Funnel-step → Meta-event mapping (plan §5.3)
  * Debug-mode TTL helper logic (datetime math is server-side per scope §C)

These are pure-Python unit tests — no DB, no httpx. Wave 1C frontend
should be able to rely on the same field names & errors.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.api.v1.schemas.tenant.tracking import (
    ConsentSettings,
    SaveMetaTrackingRequest,
    SendMetaTestEventRequest,
)
from src.infrastructure.messaging.tasks.meta_capi import (
    FUNNEL_STEP_TO_META_EVENT,
    _funnel_step_to_meta_event,
)

# ---------------------------------------------------------------------------
# pixel_id validation
# ---------------------------------------------------------------------------


class TestPixelIdValidation:
    """Pixel IDs are 15-16 numeric digits — the regex must reject anything else."""

    def test_valid_15_digits(self):
        req = SaveMetaTrackingRequest(
            pixel_id="123456789012345",
            pixel_enabled=True,
            capi_enabled=False,
        )
        assert req.pixel_id == "123456789012345"

    def test_valid_16_digits(self):
        req = SaveMetaTrackingRequest(
            pixel_id="1234567890123456",
            pixel_enabled=True,
            capi_enabled=False,
        )
        assert req.pixel_id == "1234567890123456"

    def test_too_short_rejected(self):
        with pytest.raises(ValidationError):
            SaveMetaTrackingRequest(
                pixel_id="12345",
                pixel_enabled=True,
                capi_enabled=False,
            )

    def test_too_long_rejected(self):
        with pytest.raises(ValidationError):
            SaveMetaTrackingRequest(
                pixel_id="12345678901234567",
                pixel_enabled=True,
                capi_enabled=False,
            )

    def test_non_digit_rejected(self):
        with pytest.raises(ValidationError):
            SaveMetaTrackingRequest(
                pixel_id="123456789012345a",
                pixel_enabled=True,
                capi_enabled=False,
            )


# ---------------------------------------------------------------------------
# test_event_code validation
# ---------------------------------------------------------------------------


class TestTestEventCode:
    """test_event_code must match ^TEST\\d+$ — anything else is a footgun."""

    def test_valid_code(self):
        req = SendMetaTestEventRequest(test_event_code="TEST12345")
        assert req.test_event_code == "TEST12345"

    def test_lowercase_test_rejected(self):
        with pytest.raises(ValidationError):
            SendMetaTestEventRequest(test_event_code="test12345")

    def test_no_digits_rejected(self):
        with pytest.raises(ValidationError):
            SendMetaTestEventRequest(test_event_code="TEST")

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            SendMetaTestEventRequest(test_event_code="")

    def test_save_request_accepts_none(self):
        # On the SaveMetaTrackingRequest, test_event_code is optional —
        # None is the "clear it" signal, empty string normalizes to None.
        req = SaveMetaTrackingRequest(
            pixel_id="123456789012345",
            pixel_enabled=True,
            capi_enabled=False,
            test_event_code=None,
        )
        assert req.test_event_code is None
        req2 = SaveMetaTrackingRequest(
            pixel_id="123456789012345",
            pixel_enabled=True,
            capi_enabled=False,
            test_event_code="",
        )
        assert req2.test_event_code is None


# ---------------------------------------------------------------------------
# Funnel-step → Meta-event mapping (plan §5.3)
# ---------------------------------------------------------------------------


class TestFunnelStepMapping:
    """The mapping from NUMU funnel steps to Meta event names is load-bearing
    — drift here means CAPI events won't match Pixel events for dedup."""

    def test_page_view_maps_to_PageView(self):
        assert _funnel_step_to_meta_event("page_view") == "PageView"

    def test_product_view_maps_to_ViewContent(self):
        assert _funnel_step_to_meta_event("product_view") == "ViewContent"

    def test_add_to_cart_maps_to_AddToCart(self):
        assert _funnel_step_to_meta_event("add_to_cart") == "AddToCart"

    def test_checkout_started_maps_to_InitiateCheckout(self):
        assert _funnel_step_to_meta_event("checkout_started") == "InitiateCheckout"

    def test_order_completed_maps_to_Purchase(self):
        # Defensive — webhook is normally the source for Purchase, but
        # /track will accept it too and the dedup constraint sorts it out.
        assert _funnel_step_to_meta_event("order_completed") == "Purchase"

    def test_search_maps_to_Search(self):
        assert _funnel_step_to_meta_event("search") == "Search"

    def test_lead_maps_to_Lead(self):
        assert _funnel_step_to_meta_event("lead") == "Lead"

    def test_complete_registration_maps_to_CompleteRegistration(self):
        assert (
            _funnel_step_to_meta_event("complete_registration")
            == "CompleteRegistration"
        )

    def test_add_payment_info_maps_to_AddPaymentInfo(self):
        assert _funnel_step_to_meta_event("add_payment_info") == "AddPaymentInfo"

    def test_unknown_step_returns_none(self):
        assert _funnel_step_to_meta_event("garbage") is None
        # An empty string is also a no-op.
        assert _funnel_step_to_meta_event("") is None

    def test_full_mapping_keys_match_funnel_vocab(self):
        # Sanity: every Meta event name in the mapping must be one of
        # Meta's supported standard events. The set grows over time:
        #   - Wave 1: 5 conversion-funnel events
        #   - Phase 2 (within Wave 1): +Search, Lead, CompleteRegistration,
        #     AddPaymentInfo
        #   - Wave 4 Phase 22: +Subscribe, Contact, AddToWishlist,
        #     CustomizeProduct
        assert set(FUNNEL_STEP_TO_META_EVENT.values()) == {
            # Conversion funnel
            "PageView",
            "ViewContent",
            "AddToCart",
            "InitiateCheckout",
            "Purchase",
            # Phase 2
            "Search",
            "Lead",
            "CompleteRegistration",
            "AddPaymentInfo",
            # Phase 22
            "Subscribe",
            "Contact",
            "AddToWishlist",
            "CustomizeProduct",
        }

    # Phase 22 — pin per-event mappings so a future rename can't silently
    # break a single one without the test summary calling it out.

    def test_subscribe_maps_to_Subscribe(self):
        assert _funnel_step_to_meta_event("subscribe") == "Subscribe"

    def test_contact_maps_to_Contact(self):
        assert _funnel_step_to_meta_event("contact") == "Contact"

    def test_add_to_wishlist_maps_to_AddToWishlist(self):
        assert _funnel_step_to_meta_event("add_to_wishlist") == "AddToWishlist"

    def test_customize_product_maps_to_CustomizeProduct(self):
        assert _funnel_step_to_meta_event("customize_product") == "CustomizeProduct"


# ---------------------------------------------------------------------------
# Debug-mode expiry math (server-side per scope §C)
# ---------------------------------------------------------------------------


class TestDebugModeExpiry:
    """The dashboard sends ``debug_mode: bool``; the server stores
    ``debug_mode_expires_at = now + 60min``. The Celery task reads that
    timestamp to decide whether to attach the test_event_code.

    These tests pin the math so a future refactor doesn't accidentally
    extend or shorten the window.
    """

    def test_save_request_accepts_debug_mode_true(self):
        req = SaveMetaTrackingRequest(
            pixel_id="123456789012345",
            pixel_enabled=True,
            capi_enabled=False,
            debug_mode=True,
        )
        assert req.debug_mode is True

    def test_save_request_defaults_debug_mode_false(self):
        req = SaveMetaTrackingRequest(
            pixel_id="123456789012345",
            pixel_enabled=True,
            capi_enabled=False,
        )
        assert req.debug_mode is False

    def test_expiry_window_is_60_minutes(self):
        from src.api.v1.routes.stores.settings import _DEBUG_MODE_TTL_MINUTES

        # The constant is the contract.
        assert _DEBUG_MODE_TTL_MINUTES == 60

    def test_iso_roundtrip(self):
        # Simulate the persist+read cycle the route + task perform.
        future = datetime.now(UTC) + timedelta(minutes=60)
        iso = future.isoformat()
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        # Within 1µs of the original
        assert abs((parsed - future).total_seconds()) < 0.001


# ===========================================================================
# Wave 3 Phase 18 — Granular Customer Privacy schema
# ===========================================================================


class TestConsentSettings:
    """ConsentSettings + SaveMetaTrackingRequest.consent_settings field."""

    def test_default_settings_minimal_construct(self):
        # Default-construct → opt-out region with all categories
        # pre-checked except sale_of_data (CCPA opt-OUT semantics).
        cs = ConsentSettings()
        assert cs.granular_enabled is False
        assert cs.region_default_mode == "force_opt_out"
        assert cs.default_analytics is True
        assert cs.default_marketing is True
        assert cs.default_preferences is True
        assert cs.default_sale_of_data is False

    def test_explicit_eu_strict_config(self):
        cs = ConsentSettings(
            granular_enabled=True,
            region_default_mode="force_opt_in",
            default_analytics=False,
            default_marketing=False,
            default_preferences=False,
            default_sale_of_data=False,
        )
        assert cs.granular_enabled is True
        assert cs.region_default_mode == "force_opt_in"
        assert cs.default_marketing is False

    def test_invalid_region_mode_rejected(self):
        with pytest.raises(ValidationError):
            ConsentSettings(region_default_mode="invalid_mode")  # type: ignore[arg-type]

    def test_save_request_accepts_consent_settings_when_omitted(self):
        # Backward-compat: pre-Phase-18 PUT bodies omit the field entirely.
        req = SaveMetaTrackingRequest(
            pixel_id="123456789012345",
            pixel_enabled=True,
            capi_enabled=False,
        )
        assert req.consent_settings is None

    def test_save_request_accepts_consent_settings_with_full_struct(self):
        req = SaveMetaTrackingRequest(
            pixel_id="123456789012345",
            pixel_enabled=True,
            capi_enabled=False,
            consent_settings=ConsentSettings(
                granular_enabled=True,
                region_default_mode="auto",
            ),
        )
        assert req.consent_settings is not None
        assert req.consent_settings.granular_enabled is True
        assert req.consent_settings.region_default_mode == "auto"

    def test_save_request_accepts_consent_settings_as_dict(self):
        # FastAPI clients (merchant-hub) send JSON dicts; Pydantic must
        # coerce automatically.
        req = SaveMetaTrackingRequest.model_validate({
            "pixel_id": "123456789012345",
            "pixel_enabled": True,
            "capi_enabled": False,
            "consent_settings": {
                "granular_enabled": True,
                "region_default_mode": "force_opt_in",
                "default_analytics": False,
                "default_marketing": False,
            },
        })
        assert req.consent_settings is not None
        assert req.consent_settings.granular_enabled is True
        assert req.consent_settings.default_analytics is False

    def test_sale_of_data_can_be_set_per_store_policy(self):
        # Merchant in CA might want to default sale_of_data ON to
        # match an "I want to opt out of sale" pre-check stance.
        cs = ConsentSettings(default_sale_of_data=True)
        assert cs.default_sale_of_data is True

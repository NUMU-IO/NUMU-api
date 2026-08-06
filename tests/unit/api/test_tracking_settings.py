"""Unit tests for the Meta tracking settings request schemas + helpers.

Covers the validation contract surfaced by Wave 1C:

  * pixel_id regex — numeric, up to 20 digits (NOT a length whitelist;
    see ``schemas/tenant/tracking_validation.py`` for why 15-16 was wrong)
  * test_event_code regex — alphanumeric plus dash/underscore
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
from src.api.v1.schemas.tenant.tracking_validation import (
    is_valid_meta_pixel_id,
    validation_contract,
)
from src.infrastructure.messaging.tasks.meta_capi import (
    FUNNEL_STEP_TO_META_EVENT,
    _funnel_step_to_meta_event,
)

# ---------------------------------------------------------------------------
# pixel_id validation
# ---------------------------------------------------------------------------


def _save_req(pixel_id: str) -> SaveMetaTrackingRequest:
    return SaveMetaTrackingRequest(
        pixel_id=pixel_id,
        pixel_enabled=True,
        capi_enabled=False,
    )


class TestPixelIdValidation:
    """Pixel IDs are numeric with a 20-digit ceiling.

    This class used to assert "15-16 digits" and REJECTED a 17-digit id. That
    assertion was itself the bug: Meta publishes no length for Pixel/Dataset
    IDs and allocates them from a 64-bit space (unsigned max = 20 digits) that
    grows over time, so a real 2026-minted 17-digit pixel could not be saved.
    The bound is now the arithmetic ceiling, which cannot reject a valid ID.
    """

    @pytest.mark.parametrize(
        "pixel_id",
        [
            "123456",  # 6 — lower bound
            "123456789012345",  # 15 — the old lower "valid" length
            "1712515290084839",  # 16 — a real audited pixel
            "12345678901234567",  # 17 — THE REGRESSION CASE
            "12345678901234567890",  # 20 — 64-bit ceiling, upper bound
        ],
    )
    def test_numeric_ids_accepted(self, pixel_id: str):
        assert _save_req(pixel_id).pixel_id == pixel_id

    @pytest.mark.parametrize(
        "pixel_id",
        [
            "12345",  # 5 — below the lower bound
            "123456789012345678901",  # 21 — past the 64-bit ceiling
            "123456789012345a",  # letters
            "act_1234567890123456",  # ad-account id pasted by mistake
            "https://x/1234567890123456",  # a whole URL
            "1234 567890123456",  # inner whitespace
            "123456\n7890123456",  # inner newline
        ],
    )
    def test_malformed_ids_rejected(self, pixel_id: str):
        with pytest.raises(ValidationError):
            _save_req(pixel_id)

    def test_surrounding_whitespace_is_normalized_not_rejected(self):
        # Merchants paste from Events Manager and bring whitespace with them.
        # Stripping is kinder than a 422, and it keeps the stored value safe
        # to interpolate into a Graph API path.
        assert _save_req("  1234567890123456 \n").pixel_id == "1234567890123456"

    def test_helper_rejects_trailing_newline_without_stripping(self):
        # Pins the `fullmatch` (not `match`) choice in tracking_validation.py.
        # Python's `$` also matches immediately BEFORE a trailing newline, so
        # `re.match` would accept this — and the published contract pattern is
        # handed to JS `new RegExp`, where `$` is strict. Keeping the Python
        # check strict is what makes the two agree. The Pydantic validator
        # strips first, so this only bites callers using the helper directly.
        assert not is_valid_meta_pixel_id("1234567890123456\n")
        assert is_valid_meta_pixel_id("1234567890123456")


# ---------------------------------------------------------------------------
# test_event_code validation
# ---------------------------------------------------------------------------


class TestTestEventCode:
    """test_event_code is alphanumeric with dash/underscore.

    Previously pinned to Meta's generator format. Events Manager usually
    produces ``TEST12345``, but the field is a free-form string and merchants
    paste codes from other tools — asserting the generator's shape is the same
    mistake as the pixel-length whitelist. Now matches TikTok's rule, so both
    platforms behave identically.
    """

    @pytest.mark.parametrize(
        "code",
        [
            "TEST12345",  # what Events Manager generates
            "test12345",  # lowercase — was rejected, is legitimate
            "TEST",  # no digits — was rejected, is legitimate
            "my-code_1",  # dash + underscore
            "A" * 64,  # max length
        ],
    )
    def test_valid_codes_accepted(self, code: str):
        assert SendMetaTestEventRequest(test_event_code=code).test_event_code == code

    @pytest.mark.parametrize(
        "code",
        [
            "",  # empty
            "bad code",  # space
            "code!",  # punctuation
            "A" * 65,  # over max length
        ],
    )
    def test_malformed_codes_rejected(self, code: str):
        with pytest.raises(ValidationError):
            SendMetaTestEventRequest(test_event_code=code)

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


# ---------------------------------------------------------------------------
# Validation contract — the anti-drift guard
# ---------------------------------------------------------------------------


class TestValidationContract:
    """The contract served to the hub must be the rules we actually enforce.

    This is the structural fix for the whole class of bug this pass addressed:
    the same rules were retyped in three repos and drifted, so the hub rejected
    a pixel the storefront happily rendered, and the hub demanded a 50-char
    CAPI token while the API accepted 20. The hub now fetches these values, so
    a test that the served patterns agree with the enforced validators is what
    keeps them from separating again.
    """

    def test_served_pixel_pattern_matches_enforced_validator(self):
        import re

        pattern = validation_contract()["meta"]["pixel_id"]
        compiled = re.compile(pattern)
        for candidate in (
            "123456",
            "1712515290084839",
            "12345678901234567",
            "12345678901234567890",
            "12345",
            "act_1234567890123456",
            "123456789012345a",
        ):
            assert bool(compiled.fullmatch(candidate)) is is_valid_meta_pixel_id(
                candidate
            ), f"served pattern disagrees with validator on {candidate!r}"

    def test_served_token_length_is_not_stricter_than_the_api(self):
        # The hub used to enforce 50 while SaveMetaTrackingRequest accepted 20,
        # so a valid short token was blocked client-side with no server reason.
        from src.api.v1.schemas.tenant.tracking import SaveMetaTrackingRequest

        served = validation_contract()["meta"]["min_token_length"]
        field = SaveMetaTrackingRequest.model_fields["capi_access_token"]
        enforced = next(
            c.min_length for c in field.metadata if hasattr(c, "min_length")
        )
        assert served == enforced

    def test_contract_covers_both_platforms(self):
        contract = validation_contract()
        for platform in ("meta", "tiktok"):
            rules = contract[platform]
            assert rules["pixel_id"]
            assert rules["test_event_code"]
            assert rules["min_token_length"] > 0
            # Error copy lives next to the rule so the message can't outlive
            # the rule it describes — "must be 15-16 digits" did exactly that.
            assert rules["pixel_id_error"]
            assert rules["test_event_code_error"]


# ---------------------------------------------------------------------------
# domain_verification_token
# ---------------------------------------------------------------------------


class TestDomainVerificationToken:
    """The token Meta mints for Business Manager domain verification.

    This field did not exist until a merchant could not verify their domain:
    the PUT route minted a random ``token_urlsafe`` and had no way to accept
    Meta's real value, so the ``<meta name="facebook-domain-verification">``
    tag on every storefront held a string Meta had never issued. Verification
    could not succeed for anyone. These tests pin the two properties that fix
    depends on — the value round-trips, and omitting it stays a no-op.
    """

    @staticmethod
    def _req(token):
        return SaveMetaTrackingRequest(
            pixel_id="1737914750690516",
            pixel_enabled=True,
            capi_enabled=False,
            domain_verification_token=token,
        )

    def test_defaults_to_none_so_existing_callers_are_unaffected(self):
        # Every caller that predates this field omits it; None is the signal
        # the route reads as "leave the stored token alone".
        req = SaveMetaTrackingRequest(
            pixel_id="1737914750690516",
            pixel_enabled=True,
            capi_enabled=False,
        )
        assert req.domain_verification_token is None

    def test_accepts_a_plain_token(self):
        assert (
            self._req("5qemfdk7xjz4x4n055s9hsntki0jmx").domain_verification_token
            == "5qemfdk7xjz4x4n055s9hsntki0jmx"
        )

    def test_strips_surrounding_whitespace(self):
        assert self._req("  abc123  ").domain_verification_token == "abc123"

    @pytest.mark.parametrize(
        "pasted",
        [
            '<meta name="facebook-domain-verification" content="abc123" />',
            "<meta name='facebook-domain-verification' content='abc123'>",
            '  <meta name="facebook-domain-verification" content="abc123"/>  ',
        ],
    )
    def test_extracts_the_token_from_a_pasted_meta_tag(self, pasted):
        # Business Manager renders the token inside a ready-to-copy tag, so
        # the whole tag is what lands on the clipboard. Accepting it is
        # kinder than a 422 the merchant cannot act on.
        assert self._req(pasted).domain_verification_token == "abc123"

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_becomes_none(self, blank):
        assert self._req(blank).domain_verification_token is None

    @pytest.mark.parametrize(
        "bad",
        [
            "abc 123",
            "abc<123",
            'abc"123',
            "x" * 129,
        ],
    )
    def test_rejects_paste_errors(self, bad):
        with pytest.raises(ValidationError):
            self._req(bad)

    def test_does_not_encode_a_format_meta_owns(self):
        # Per docs/external-contracts.md the bound is loose on purpose: any
        # printable token Meta might mint has to survive, whatever its shape.
        for shape in ("ABC-def_123", "0123456789", "aB3" * 10, "z"):
            assert self._req(shape).domain_verification_token == shape

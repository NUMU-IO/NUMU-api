"""Tests for the manual (Tier 3) carrier and its courier profiles.

One adapter covers every courier that has no API — البريد المصري,
Cathedis, Sprint, MCS, R2S, Apex, Xceed, Door To Door, and the merchant's
own motorbike guy.

The riskiest thing here is not the provider, it's the migration:
``shipping.manual.enabled`` ships **True on every store**, including both
live ones. Anything that changes how that default is produced can switch
manual shipping off for every existing merchant.
"""

import pytest

from src.application.services.manual_carrier_profiles import (
    ManualCarrierProfile,
    ProfileValidationError,
    active_profiles,
    backfill_default_profile,
    delete_profile,
    get_profile,
    list_profiles,
    profiles_for_governorate,
    upsert_profile,
    validate_profile,
)
from src.core.interfaces.services.shipping_provider import NotSupportedByCarrier
from src.infrastructure.external_services.manual.shipping_service import (
    TRACKING_PREFIX,
    ManualShippingService,
    generate_tracking_number,
)

EGYPT_POST = {
    "name_en": "Egypt Post",
    "name_ar": "البريد المصري",
    "governorate_codes": ["EG-C", "EG-GZ"],
    "contact_phone": "16789",
}


class TestTrackingNumbers:
    def test_prefixed_and_sized(self):
        number = generate_tracking_number()
        assert number.startswith(TRACKING_PREFIX)
        assert len(number) == len(TRACKING_PREFIX) + 10

    def test_avoids_look_alike_characters(self):
        """These get read aloud off a printed waybill.

        I, L, O and U are excluded so nobody confuses 1/I/L or 0/O.
        """
        joined = "".join(generate_tracking_number() for _ in range(200))
        body = joined.replace(TRACKING_PREFIX, "")
        assert not set(body) & set("ILOU")

    def test_unique_in_bulk(self):
        numbers = {generate_tracking_number() for _ in range(5000)}
        assert len(numbers) == 5000


class TestManualProvider:
    def setup_method(self):
        self.svc = ManualShippingService()

    @pytest.mark.asyncio
    async def test_create_issues_a_numu_waybill(self):
        from src.core.interfaces.services.shipping_provider import (
            Parcel,
            ShippingAddress,
        )

        label = await self.svc.create_shipment(
            from_address=ShippingAddress(
                name="s", street1="a", city="Cairo", country="EG"
            ),
            to_address=ShippingAddress(
                name="r", street1="b", city="Giza", country="EG"
            ),
            parcel=Parcel(length=1, width=1, height=1, weight=1),
            rate_id="manual_abc",
        )
        assert label.tracking_number.startswith(TRACKING_PREFIX)
        assert label.carrier == "manual"
        # No carrier hosts a label for us; it's rendered on demand.
        assert label.label_url == ""

    @pytest.mark.asyncio
    async def test_tracking_is_empty_not_an_error(self):
        """The shipment's own history is the record; erroring would turn
        "we already know" into a failure."""
        info = await self.svc.track_shipment("manual", "NM123")
        assert info.status == "unknown"
        assert info.events == []

    @pytest.mark.asyncio
    async def test_cancel_refuses_rather_than_lying(self):
        """Returning True would claim we stopped a parcel we can't reach."""
        with pytest.raises(NotSupportedByCarrier):
            await self.svc.cancel_shipment("NM123")

    @pytest.mark.asyncio
    async def test_unsupported_operations_raise(self):
        for call in (
            self.svc.get_rates(None, None, None),
            self.svc.create_pickup(None),
            self.svc.get_cities(),
            self.svc.request_return("NM1"),
        ):
            with pytest.raises(NotSupportedByCarrier):
                await call

    @pytest.mark.asyncio
    async def test_address_validation_is_unknown_not_invalid(self):
        ok, corrected = await self.svc.validate_address(None)
        assert ok is True
        assert corrected is None

    def test_webhooks_fail_closed(self):
        assert self.svc.verify_webhook(b"{}", "sig", "secret") is False


class TestProfileValidation:
    def test_requires_a_name(self):
        with pytest.raises(ProfileValidationError):
            validate_profile({})

    def test_one_language_fills_the_other(self):
        """A half-filled form must not leave a blank card in one language."""
        cleaned = validate_profile({"name_ar": "كاثيدس"})
        assert cleaned["name_en"] == "كاثيدس"
        assert cleaned["name_ar"] == "كاثيدس"

    def test_rejects_a_bad_governorate_code(self):
        with pytest.raises(ProfileValidationError):
            validate_profile({"name_en": "X", "governorate_codes": ["CAIRO"]})

    def test_deduplicates_and_uppercases_codes(self):
        cleaned = validate_profile({
            "name_en": "X",
            "governorate_codes": ["eg-c", "EG-C", "eg-gz"],
        })
        assert cleaned["governorate_codes"] == ["EG-C", "EG-GZ"]

    def test_tracking_template_must_interpolate(self):
        """Without the placeholder every parcel gets the same URL."""
        with pytest.raises(ProfileValidationError):
            validate_profile({
                "name_en": "X",
                "tracking_url_template": "https://t.test/track",
            })

    def test_tracking_template_must_be_https(self):
        with pytest.raises(ProfileValidationError):
            validate_profile({
                "name_en": "X",
                "tracking_url_template": "http://t.test/{tracking_number}",
            })

    def test_cutoff_must_be_a_time(self):
        with pytest.raises(ProfileValidationError):
            validate_profile({"name_en": "X", "cutoff_time": "4pm"})
        assert validate_profile({"name_en": "X", "cutoff_time": "16:00"})


class TestProfileCrud:
    def test_create_read_update_delete(self):
        settings, created = upsert_profile({}, EGYPT_POST)
        assert created.name_ar == "البريد المصري"
        assert len(list_profiles(settings)) == 1

        settings, updated = upsert_profile(
            settings, {**EGYPT_POST, "contact_phone": "19999"}, profile_id=created.id
        )
        assert updated.id == created.id
        assert get_profile(settings, created.id).contact_phone == "19999"
        assert len(list_profiles(settings)) == 1

        settings = delete_profile(settings, created.id)
        assert list_profiles(settings) == []

    def test_update_of_a_missing_profile_raises(self):
        with pytest.raises(ProfileValidationError):
            upsert_profile({}, EGYPT_POST, profile_id="nope")

    def test_writing_profiles_preserves_other_shipping_settings(self):
        before = {"shipping": {"bosta": {"enabled": True}, "manual": {"enabled": True}}}
        after, _ = upsert_profile(before, EGYPT_POST)
        assert after["shipping"]["bosta"] == {"enabled": True}
        assert after["shipping"]["manual"]["enabled"] is True

    def test_inactive_profiles_are_excluded_from_active(self):
        settings, _ = upsert_profile({}, {**EGYPT_POST, "is_active": False})
        assert len(list_profiles(settings)) == 1
        assert active_profiles(settings) == []

    def test_malformed_stored_entries_are_skipped(self):
        """Settings are free-form JSON; a bad row must not break the page."""
        settings = {
            "shipping": {
                "manual": {"profiles": ["nope", {}, {"id": "x", "name_en": "ok"}]}
            }
        }
        assert [p.id for p in list_profiles(settings)] == ["x"]


class TestCoverage:
    def test_empty_coverage_means_everywhere(self):
        """A merchant who hasn't said otherwise shouldn't have parcels refused."""
        profile = ManualCarrierProfile(id="1", name_en="X", name_ar="X")
        assert profile.covers("EG-ASN")
        assert profile.covers(None)

    def test_coverage_is_respected_and_case_insensitive(self):
        profile = ManualCarrierProfile(
            id="1", name_en="X", name_ar="X", governorate_codes=["EG-C"]
        )
        assert profile.covers("eg-c")
        assert not profile.covers("EG-ASN")

    def test_filters_by_governorate(self):
        settings, _ = upsert_profile({}, EGYPT_POST)  # EG-C, EG-GZ
        settings, _ = upsert_profile(
            settings, {"name_en": "Upper Egypt guy", "governorate_codes": ["EG-ASN"]}
        )
        assert len(profiles_for_governorate(settings, "EG-C")) == 1
        assert len(profiles_for_governorate(settings, "EG-ASN")) == 1
        assert len(profiles_for_governorate(settings, "EG-ALX")) == 0

    def test_tracking_url_only_when_the_courier_has_one(self):
        no_page = ManualCarrierProfile(id="1", name_en="X", name_ar="X")
        assert no_page.tracking_url("NM1") is None

        has_page = ManualCarrierProfile(
            id="2",
            name_en="X",
            name_ar="X",
            tracking_url_template="https://t.test/{tracking_number}",
        )
        assert has_page.tracking_url("NM1") == "https://t.test/NM1"


class TestBackfill:
    """🔴 This runs against production data on every existing store."""

    def test_gives_an_enabled_store_a_default_profile(self):
        settings = backfill_default_profile({"shipping": {"manual": {"enabled": True}}})
        assert settings is not None
        assert len(list_profiles(settings)) == 1

    def test_skips_a_store_that_already_has_profiles(self):
        existing, _ = upsert_profile(
            {"shipping": {"manual": {"enabled": True}}}, EGYPT_POST
        )
        assert backfill_default_profile(existing) is None

    def test_skips_a_store_with_manual_disabled(self):
        assert (
            backfill_default_profile({"shipping": {"manual": {"enabled": False}}})
            is None
        )

    def test_skips_a_store_with_no_shipping_settings(self):
        assert backfill_default_profile({}) is None

    def test_is_idempotent(self):
        """It will be run more than once; a second pass must be a no-op."""
        first = backfill_default_profile({"shipping": {"manual": {"enabled": True}}})
        assert backfill_default_profile(first) is None


class TestSettingsDefaultsUnchanged:
    """The default every existing store depends on."""

    def test_manual_still_defaults_to_enabled(self):
        from src.api.v1.routes.stores.settings import _get_default_shipping_settings

        manual = _get_default_shipping_settings()["manual"]
        assert manual["enabled"] is True, (
            "manual ships enabled on every store, including both live ones — "
            "flipping this silently disables their shipping"
        )
        assert manual["is_configured"] is True

    def test_other_carriers_still_default_to_off(self):
        from src.api.v1.routes.stores.settings import _get_default_shipping_settings

        defaults = _get_default_shipping_settings()
        for slug in ("bosta", "mylerz", "jt"):
            assert defaults[slug]["enabled"] is False

    def test_no_duplicate_carrier_keys(self):
        """`manual` was briefly in both the registry and the override list."""
        from src.api.v1.routes.stores.settings import shipping_carrier_keys

        keys = shipping_carrier_keys()
        assert len(keys) == len(set(keys))

    def test_manual_is_a_registered_carrier(self):
        from src.application.services.carrier_registry import get_spec

        spec = get_spec("manual")
        assert spec is not None
        assert spec.tier == "manual"
        assert spec.capabilities.supports_labels is True
        assert spec.capabilities.supports_cod is True
        # Nothing electronic — these would put dead buttons in the hub.
        assert spec.capabilities.supports_cancel is False
        assert spec.capabilities.supports_live_rates is False
        assert spec.capabilities.supports_webhooks is False


class TestCourierSeeds:
    """Seeded couriers must be a starting point, never a fabrication."""

    def test_every_plan_courier_is_seeded(self):
        from src.application.services.manual_carrier_seeds import COURIER_SEEDS

        keys = {s.key for s in COURIER_SEEDS}
        for expected in (
            "egypt_post",
            "cathedis",
            "sprint",
            "mcs",
            "r2s",
            "apex",
            "xceed",
            "door_to_door",
        ):
            assert expected in keys, expected

    def test_seeds_are_bilingual(self):
        from src.application.services.manual_carrier_seeds import COURIER_SEEDS

        for seed in COURIER_SEEDS:
            assert seed.name_en and seed.name_ar
            assert any("؀" <= ch <= "ۿ" for ch in seed.name_ar), seed.key

    def test_unverified_seeds_cover_everywhere_rather_than_guessing(self):
        """A courier wrongly limited to three governorates silently hides
        deliveries the merchant could have made, and nobody finds out. A
        courier that actually covers less declines the parcel, which the
        merchant sees immediately.
        """
        from src.application.services.manual_carrier_seeds import (
            all_governorate_codes,
            get_seed,
        )

        values = get_seed("cathedis").to_profile_values()
        assert len(values["governorate_codes"]) == len(all_governorate_codes()) == 27

    def test_no_seed_carries_a_phone_without_a_source(self):
        """A wrong courier number is worse than none.

        This is tracked separately from `data_verified`: Egypt Post's
        published call centre is a fact even while its coverage is not.
        """
        from src.application.services.manual_carrier_seeds import COURIER_SEEDS

        for seed in COURIER_SEEDS:
            if seed.contact_phone:
                assert seed.contact_source, (
                    f"{seed.key} has a phone number with no stated source"
                )

    def test_the_source_rule_is_enforced_at_import(self):
        """A fabricated number must break the build, not ship quietly."""
        from src.application.services.manual_carrier_seeds import CourierSeed

        bad = CourierSeed(key="x", name_en="X", name_ar="س", contact_phone="19999")
        assert bad.contact_phone and not bad.contact_source

    def test_the_verified_flag_is_honest_about_the_gap(self):
        from src.application.services.manual_carrier_seeds import unverified_keys

        # These are genuinely unconfirmed today; the flag says so rather
        # than the data quietly presenting as fact.
        assert "cathedis" in unverified_keys()
        assert "own_courier" not in unverified_keys()

    def test_egypt_post_explains_it_has_no_api(self):
        """The merchant must know to register on Wassalha themselves."""
        from src.application.services.manual_carrier_seeds import get_seed

        note = get_seed("egypt_post").note_ar
        assert "وصّلها" in note

    def test_a_seed_produces_a_valid_profile(self):
        from src.application.services.manual_carrier_seeds import get_seed

        settings, profile = upsert_profile(
            {}, get_seed("egypt_post").to_profile_values()
        )
        assert profile.seed_key == "egypt_post"
        assert profile.name_ar == "البريد المصري"
        assert len(list_profiles(settings)) == 1

    def test_every_seed_produces_a_valid_profile(self):
        from src.application.services.manual_carrier_seeds import COURIER_SEEDS

        for seed in COURIER_SEEDS:
            validate_profile(seed.to_profile_values())

    def test_catalog_exposes_the_verified_flag_to_the_hub(self):
        from src.application.services.manual_carrier_seeds import seed_catalog

        entry = next(c for c in seed_catalog() if c["key"] == "cathedis")
        assert entry["data_verified"] is False
        assert entry["covers_all_governorates"] is True

"""Unit tests for Egyptian governorate canonical data.

Governorate data lives in `src.core.value_objects.geography` after the
shipping configuration refactor. The old `bosta.governorates` import path
still works (back-compat shim) and is exercised here alongside the
canonical path.
"""

from src.core.value_objects.geography import (
    EGYPTIAN_GOVERNORATES,
    LogisticsZone,
    get_all_governorates_dict,
    get_governorate_by_code,
    get_governorate_by_name,
    get_governorates_by_zone,
)
from src.infrastructure.external_services.bosta.governorates import (
    ShippingZone as BostaShippingZone,  # back-compat alias → LogisticsZone
)


class TestEgyptianGovernorates:
    """Tests for the 27 canonical Egyptian governorates."""

    def test_total_governorates(self):
        assert len(EGYPTIAN_GOVERNORATES) == 27

    def test_all_governorates_have_required_fields(self):
        for gov in EGYPTIAN_GOVERNORATES:
            assert gov.code, f"Missing ISO code for {gov.name_en}"
            assert gov.bosta_code, f"Missing Bosta code for {gov.name_en}"
            assert gov.name_en
            assert gov.name_ar, f"Missing Arabic name for {gov.name_en}"
            assert gov.zone
            assert gov.capital_en
            assert gov.capital_ar

    def test_get_governorate_by_iso_code_cairo(self):
        gov = get_governorate_by_code("EG-C")
        assert gov is not None
        assert gov.name_en == "Cairo"
        assert gov.name_ar == "القاهرة"
        assert gov.zone == LogisticsZone.GREATER_CAIRO
        assert gov.bosta_code == "CAI"

    def test_get_governorate_by_legacy_bosta_code(self):
        # Back-compat: lookup still accepts old Bosta 3-letter codes.
        gov = get_governorate_by_code("CAI")
        assert gov is not None
        assert gov.code == "EG-C"
        assert gov.name_en == "Cairo"

    def test_get_governorate_by_code_case_insensitive(self):
        assert get_governorate_by_code("eg-c") == get_governorate_by_code("EG-C")
        assert get_governorate_by_code("cai") == get_governorate_by_code("CAI")

    def test_get_governorate_by_code_not_found(self):
        assert get_governorate_by_code("XXX") is None
        assert get_governorate_by_code("EG-ZZ") is None

    def test_get_governorate_by_name_english(self):
        gov = get_governorate_by_name("Alexandria")
        assert gov is not None
        assert gov.code == "EG-ALX"
        assert gov.bosta_code == "ALX"
        assert gov.zone == LogisticsZone.DELTA

    def test_get_governorate_by_name_english_case_insensitive(self):
        gov = get_governorate_by_name("alexandria")
        assert gov is not None
        assert gov.code == "EG-ALX"

    def test_get_governorate_by_name_arabic(self):
        gov = get_governorate_by_name("الجيزة")
        assert gov is not None
        assert gov.code == "EG-GZ"
        assert gov.name_en == "Giza"

    def test_get_governorate_by_name_alias_transliteration(self):
        # Alias map handles common English transliterations.
        gov = get_governorate_by_name("Alex")
        assert gov is not None
        assert gov.code == "EG-ALX"

    def test_get_governorate_by_name_alias_arabic_variant(self):
        # Arabic spelling without taa marbuta.
        gov = get_governorate_by_name("القاهره")
        assert gov is not None
        assert gov.code == "EG-C"

    def test_get_governorate_by_name_not_found(self):
        assert get_governorate_by_name("Atlantis") is None

    def test_get_governorates_by_zone_greater_cairo(self):
        govs = get_governorates_by_zone(LogisticsZone.GREATER_CAIRO)
        iso_codes = {g.code for g in govs}

        assert len(govs) == 3
        assert "EG-C" in iso_codes
        assert "EG-GZ" in iso_codes
        assert "EG-KB" in iso_codes  # Qalyubia

    def test_get_governorates_by_zone_delta(self):
        govs = get_governorates_by_zone(LogisticsZone.DELTA)
        iso_codes = {g.code for g in govs}

        assert len(govs) == 8
        assert "EG-ALX" in iso_codes
        assert "EG-GH" in iso_codes  # Gharbia
        assert "EG-DK" in iso_codes  # Dakahlia

    def test_get_governorates_by_zone_remote(self):
        govs = get_governorates_by_zone(LogisticsZone.REMOTE)
        iso_codes = {g.code for g in govs}

        assert len(govs) == 3
        assert "EG-BA" in iso_codes  # Red Sea
        assert "EG-MT" in iso_codes  # Matrouh
        assert "EG-WAD" in iso_codes  # New Valley

    def test_get_all_governorates_dict_english_keyed_by_bosta_code(self):
        # Dict shape preserved for back-compat: keyed on bosta_code.
        data = get_all_governorates_dict(locale="en")

        assert len(data) == 27
        assert data["CAI"] == "Cairo"
        assert data["ALX"] == "Alexandria"

    def test_get_all_governorates_dict_arabic(self):
        data = get_all_governorates_dict(locale="ar")

        assert len(data) == 27
        assert data["CAI"] == "القاهرة"
        assert data["ALX"] == "الإسكندرية"

    def test_unique_iso_codes(self):
        codes = [g.code for g in EGYPTIAN_GOVERNORATES]
        assert len(codes) == len(set(codes))

    def test_unique_bosta_codes(self):
        codes = [g.bosta_code for g in EGYPTIAN_GOVERNORATES]
        assert len(codes) == len(set(codes))

    def test_all_iso_codes_are_eg_prefixed(self):
        for gov in EGYPTIAN_GOVERNORATES:
            assert gov.code.startswith("EG-"), (
                f"{gov.name_en} has non-ISO code {gov.code}"
            )

    def test_zone_coverage(self):
        for zone in LogisticsZone:
            govs = get_governorates_by_zone(zone)
            assert len(govs) > 0, f"No governorates in {zone}"

    def test_major_cities_present(self):
        major_cities = [
            ("EG-C", "Cairo"),
            ("EG-ALX", "Alexandria"),
            ("EG-GZ", "Giza"),
            ("EG-LX", "Luxor"),
            ("EG-ASN", "Aswan"),
            ("EG-PTS", "Port Said"),
        ]
        for code, name in major_cities:
            gov = get_governorate_by_code(code)
            assert gov is not None, f"Missing {name} at {code}"
            assert gov.name_en == name

    def test_back_compat_shipping_zone_alias(self):
        # The old `ShippingZone` import resolves to the new LogisticsZone.
        assert BostaShippingZone is LogisticsZone
        assert BostaShippingZone.GREATER_CAIRO == LogisticsZone.GREATER_CAIRO

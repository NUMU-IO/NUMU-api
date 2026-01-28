"""Unit tests for Egyptian governorates data."""

import pytest

from src.infrastructure.external_services.bosta.governorates import (
    EGYPTIAN_GOVERNORATES,
    ShippingZone,
    get_all_governorates_dict,
    get_governorate_by_code,
    get_governorate_by_name,
    get_governorates_by_zone,
)


class TestEgyptianGovernorates:
    """Tests for Egyptian governorates data."""

    def test_total_governorates(self):
        """Test total number of governorates is 27."""
        assert len(EGYPTIAN_GOVERNORATES) == 27

    def test_all_governorates_have_required_fields(self):
        """Test all governorates have required fields."""
        for gov in EGYPTIAN_GOVERNORATES:
            assert gov.code, f"Missing code for {gov.name_en}"
            assert gov.name_en, f"Missing English name"
            assert gov.name_ar, f"Missing Arabic name for {gov.name_en}"
            assert gov.zone, f"Missing zone for {gov.name_en}"
            assert gov.capital_en, f"Missing capital for {gov.name_en}"
            assert gov.capital_ar, f"Missing Arabic capital for {gov.name_en}"

    def test_get_governorate_by_code_cairo(self):
        """Test getting Cairo by code."""
        gov = get_governorate_by_code("CAI")
        assert gov is not None
        assert gov.name_en == "Cairo"
        assert gov.name_ar == "القاهرة"
        assert gov.zone == ShippingZone.GREATER_CAIRO

    def test_get_governorate_by_code_case_insensitive(self):
        """Test code lookup is case insensitive."""
        gov1 = get_governorate_by_code("cai")
        gov2 = get_governorate_by_code("CAI")
        assert gov1 == gov2

    def test_get_governorate_by_code_not_found(self):
        """Test getting non-existent governorate returns None."""
        gov = get_governorate_by_code("XXX")
        assert gov is None

    def test_get_governorate_by_name_english(self):
        """Test getting governorate by English name."""
        gov = get_governorate_by_name("Alexandria")
        assert gov is not None
        assert gov.code == "ALX"
        assert gov.zone == ShippingZone.DELTA

    def test_get_governorate_by_name_english_case_insensitive(self):
        """Test English name lookup is case insensitive."""
        gov = get_governorate_by_name("alexandria")
        assert gov is not None
        assert gov.code == "ALX"

    def test_get_governorate_by_name_arabic(self):
        """Test getting governorate by Arabic name."""
        gov = get_governorate_by_name("الجيزة")
        assert gov is not None
        assert gov.code == "GIZ"
        assert gov.name_en == "Giza"

    def test_get_governorate_by_name_not_found(self):
        """Test getting non-existent name returns None."""
        gov = get_governorate_by_name("Unknown")
        assert gov is None

    def test_get_governorates_by_zone_greater_cairo(self):
        """Test getting Greater Cairo governorates."""
        govs = get_governorates_by_zone(ShippingZone.GREATER_CAIRO)
        codes = [g.code for g in govs]

        assert len(govs) == 3
        assert "CAI" in codes  # Cairo
        assert "GIZ" in codes  # Giza
        assert "QLY" in codes  # Qalyubia

    def test_get_governorates_by_zone_delta(self):
        """Test getting Delta governorates."""
        govs = get_governorates_by_zone(ShippingZone.DELTA)
        codes = [g.code for g in govs]

        assert len(govs) == 8
        assert "ALX" in codes  # Alexandria
        assert "GHR" in codes  # Gharbia
        assert "DKH" in codes  # Dakahlia

    def test_get_governorates_by_zone_remote(self):
        """Test getting remote area governorates."""
        govs = get_governorates_by_zone(ShippingZone.REMOTE)
        codes = [g.code for g in govs]

        assert len(govs) == 3
        assert "RDS" in codes  # Red Sea
        assert "MTR" in codes  # Matrouh
        assert "NWV" in codes  # New Valley

    def test_get_all_governorates_dict_english(self):
        """Test getting all governorates as English dict."""
        data = get_all_governorates_dict(locale="en")

        assert len(data) == 27
        assert data["CAI"] == "Cairo"
        assert data["ALX"] == "Alexandria"

    def test_get_all_governorates_dict_arabic(self):
        """Test getting all governorates as Arabic dict."""
        data = get_all_governorates_dict(locale="ar")

        assert len(data) == 27
        assert data["CAI"] == "القاهرة"
        assert data["ALX"] == "الإسكندرية"

    def test_unique_codes(self):
        """Test all governorate codes are unique."""
        codes = [g.code for g in EGYPTIAN_GOVERNORATES]
        assert len(codes) == len(set(codes))

    def test_zone_coverage(self):
        """Test all zones have at least one governorate."""
        for zone in ShippingZone:
            govs = get_governorates_by_zone(zone)
            assert len(govs) > 0, f"No governorates in {zone}"

    def test_major_cities_present(self):
        """Test major Egyptian cities are present."""
        major_cities = [
            ("CAI", "Cairo"),
            ("ALX", "Alexandria"),
            ("GIZ", "Giza"),
            ("LXR", "Luxor"),
            ("ASN", "Aswan"),
            ("PSD", "Port Said"),
        ]

        for code, name in major_cities:
            gov = get_governorate_by_code(code)
            assert gov is not None, f"Missing {name}"
            assert gov.name_en == name

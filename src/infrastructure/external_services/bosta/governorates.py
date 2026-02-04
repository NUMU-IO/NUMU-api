"""Egyptian governorates data for shipping zone calculation.

Egypt has 27 governorates (محافظات). Shipping costs are typically
calculated based on zones:
- Zone 1: Greater Cairo (Cairo, Giza, Qalyubia)
- Zone 2: Alexandria & Delta
- Zone 3: Canal Zone & Sinai
- Zone 4: Upper Egypt
- Zone 5: Remote areas (Red Sea, Matrouh, etc.)
"""

from dataclasses import dataclass
from enum import StrEnum


class ShippingZone(StrEnum):
    """Shipping zones for rate calculation."""

    GREATER_CAIRO = "zone_1"
    DELTA = "zone_2"
    CANAL_SINAI = "zone_3"
    UPPER_EGYPT = "zone_4"
    REMOTE = "zone_5"


@dataclass(frozen=True)
class Governorate:
    """Egyptian governorate data."""

    code: str  # Bosta/shipping code
    name_en: str
    name_ar: str
    zone: ShippingZone
    capital_en: str
    capital_ar: str


# All 27 Egyptian governorates
EGYPTIAN_GOVERNORATES: list[Governorate] = [
    # Zone 1: Greater Cairo
    Governorate(
        code="CAI",
        name_en="Cairo",
        name_ar="القاهرة",
        zone=ShippingZone.GREATER_CAIRO,
        capital_en="Cairo",
        capital_ar="القاهرة",
    ),
    Governorate(
        code="GIZ",
        name_en="Giza",
        name_ar="الجيزة",
        zone=ShippingZone.GREATER_CAIRO,
        capital_en="Giza",
        capital_ar="الجيزة",
    ),
    Governorate(
        code="QLY",
        name_en="Qalyubia",
        name_ar="القليوبية",
        zone=ShippingZone.GREATER_CAIRO,
        capital_en="Banha",
        capital_ar="بنها",
    ),
    # Zone 2: Alexandria & Delta
    Governorate(
        code="ALX",
        name_en="Alexandria",
        name_ar="الإسكندرية",
        zone=ShippingZone.DELTA,
        capital_en="Alexandria",
        capital_ar="الإسكندرية",
    ),
    Governorate(
        code="BHR",
        name_en="Beheira",
        name_ar="البحيرة",
        zone=ShippingZone.DELTA,
        capital_en="Damanhur",
        capital_ar="دمنهور",
    ),
    Governorate(
        code="GHR",
        name_en="Gharbia",
        name_ar="الغربية",
        zone=ShippingZone.DELTA,
        capital_en="Tanta",
        capital_ar="طنطا",
    ),
    Governorate(
        code="MNF",
        name_en="Monufia",
        name_ar="المنوفية",
        zone=ShippingZone.DELTA,
        capital_en="Shebin El Kom",
        capital_ar="شبين الكوم",
    ),
    Governorate(
        code="DKH",
        name_en="Dakahlia",
        name_ar="الدقهلية",
        zone=ShippingZone.DELTA,
        capital_en="Mansoura",
        capital_ar="المنصورة",
    ),
    Governorate(
        code="DMT",
        name_en="Damietta",
        name_ar="دمياط",
        zone=ShippingZone.DELTA,
        capital_en="Damietta",
        capital_ar="دمياط",
    ),
    Governorate(
        code="SHR",
        name_en="Sharqia",
        name_ar="الشرقية",
        zone=ShippingZone.DELTA,
        capital_en="Zagazig",
        capital_ar="الزقازيق",
    ),
    Governorate(
        code="KFS",
        name_en="Kafr El Sheikh",
        name_ar="كفر الشيخ",
        zone=ShippingZone.DELTA,
        capital_en="Kafr El Sheikh",
        capital_ar="كفر الشيخ",
    ),
    # Zone 3: Canal Zone & Sinai
    Governorate(
        code="PSD",
        name_en="Port Said",
        name_ar="بورسعيد",
        zone=ShippingZone.CANAL_SINAI,
        capital_en="Port Said",
        capital_ar="بورسعيد",
    ),
    Governorate(
        code="ISM",
        name_en="Ismailia",
        name_ar="الإسماعيلية",
        zone=ShippingZone.CANAL_SINAI,
        capital_en="Ismailia",
        capital_ar="الإسماعيلية",
    ),
    Governorate(
        code="SUZ",
        name_en="Suez",
        name_ar="السويس",
        zone=ShippingZone.CANAL_SINAI,
        capital_en="Suez",
        capital_ar="السويس",
    ),
    Governorate(
        code="NSN",
        name_en="North Sinai",
        name_ar="شمال سيناء",
        zone=ShippingZone.CANAL_SINAI,
        capital_en="Arish",
        capital_ar="العريش",
    ),
    Governorate(
        code="SSN",
        name_en="South Sinai",
        name_ar="جنوب سيناء",
        zone=ShippingZone.CANAL_SINAI,
        capital_en="El Tor",
        capital_ar="الطور",
    ),
    # Zone 4: Upper Egypt
    Governorate(
        code="FYM",
        name_en="Fayoum",
        name_ar="الفيوم",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Fayoum",
        capital_ar="الفيوم",
    ),
    Governorate(
        code="BNS",
        name_en="Beni Suef",
        name_ar="بني سويف",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Beni Suef",
        capital_ar="بني سويف",
    ),
    Governorate(
        code="MNY",
        name_en="Minya",
        name_ar="المنيا",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Minya",
        capital_ar="المنيا",
    ),
    Governorate(
        code="AST",
        name_en="Asyut",
        name_ar="أسيوط",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Asyut",
        capital_ar="أسيوط",
    ),
    Governorate(
        code="SHG",
        name_en="Sohag",
        name_ar="سوهاج",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Sohag",
        capital_ar="سوهاج",
    ),
    Governorate(
        code="QNA",
        name_en="Qena",
        name_ar="قنا",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Qena",
        capital_ar="قنا",
    ),
    Governorate(
        code="LXR",
        name_en="Luxor",
        name_ar="الأقصر",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Luxor",
        capital_ar="الأقصر",
    ),
    Governorate(
        code="ASN",
        name_en="Aswan",
        name_ar="أسوان",
        zone=ShippingZone.UPPER_EGYPT,
        capital_en="Aswan",
        capital_ar="أسوان",
    ),
    # Zone 5: Remote areas
    Governorate(
        code="RDS",
        name_en="Red Sea",
        name_ar="البحر الأحمر",
        zone=ShippingZone.REMOTE,
        capital_en="Hurghada",
        capital_ar="الغردقة",
    ),
    Governorate(
        code="MTR",
        name_en="Matrouh",
        name_ar="مطروح",
        zone=ShippingZone.REMOTE,
        capital_en="Marsa Matrouh",
        capital_ar="مرسى مطروح",
    ),
    Governorate(
        code="NWV",
        name_en="New Valley",
        name_ar="الوادي الجديد",
        zone=ShippingZone.REMOTE,
        capital_en="Kharga",
        capital_ar="الخارجة",
    ),
]

# Create lookup dictionaries
_GOVERNORATE_BY_CODE = {g.code: g for g in EGYPTIAN_GOVERNORATES}
_GOVERNORATE_BY_NAME_EN = {g.name_en.lower(): g for g in EGYPTIAN_GOVERNORATES}
_GOVERNORATE_BY_NAME_AR = {g.name_ar: g for g in EGYPTIAN_GOVERNORATES}


def get_governorate_by_code(code: str) -> Governorate | None:
    """Get governorate by code.

    Args:
        code: Governorate code (e.g., "CAI")

    Returns:
        Governorate or None if not found
    """
    return _GOVERNORATE_BY_CODE.get(code.upper())


def get_governorate_by_name(name: str) -> Governorate | None:
    """Get governorate by name (English or Arabic).

    Args:
        name: Governorate name in English or Arabic

    Returns:
        Governorate or None if not found
    """
    # Try Arabic first (exact match)
    if name in _GOVERNORATE_BY_NAME_AR:
        return _GOVERNORATE_BY_NAME_AR[name]

    # Try English (case-insensitive)
    return _GOVERNORATE_BY_NAME_EN.get(name.lower())


def get_governorates_by_zone(zone: ShippingZone) -> list[Governorate]:
    """Get all governorates in a shipping zone.

    Args:
        zone: Shipping zone

    Returns:
        List of governorates in the zone
    """
    return [g for g in EGYPTIAN_GOVERNORATES if g.zone == zone]


def get_all_governorates_dict(locale: str = "en") -> dict[str, str]:
    """Get all governorates as code -> name dict.

    Args:
        locale: Language for names ("en" or "ar")

    Returns:
        Dictionary of code -> name
    """
    if locale == "ar":
        return {g.code: g.name_ar for g in EGYPTIAN_GOVERNORATES}
    return {g.code: g.name_en for g in EGYPTIAN_GOVERNORATES}

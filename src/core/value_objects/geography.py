"""Canonical geographic reference data for Egypt.

This module is the single source of truth for country / governorate /
logistics-zone identifiers used across the platform (storefront dropdowns,
merchant zone editor, courier integration, rate calculator).

Relocated from `src/infrastructure/external_services/bosta/governorates.py`
to the domain layer — it is not Bosta-specific. The old location is kept as
a back-compat shim re-exporting from here.

Wire identifiers are **ISO 3166-2** subdivision codes (e.g. "EG-C" Cairo,
"EG-GZ" Giza), chosen so adding KSA / UAE later does not collide. A parallel
`bosta_code` attribute preserves the legacy 3-letter Bosta identifier for
any integration that referenced it historically.

Do NOT edit governorate data per-store; merchants define *zones that group
governorates*. Courier handoffs require stable codes.
"""

from dataclasses import dataclass
from enum import StrEnum


class LogisticsZone(StrEnum):
    """Broad logistics zones for Egypt.

    Used as a preset / grouping only — not a hard hierarchy. Merchants
    define their own zones; this enum seeds the "Quick Start: 4-Zone
    Egypt" preset and provides a stable grouping for bulk selection
    in the merchant UI.
    """

    GREATER_CAIRO = "greater_cairo"
    DELTA = "delta"
    CANAL_SINAI = "canal_sinai"
    UPPER_EGYPT = "upper_egypt"
    REMOTE = "remote"


# Back-compat alias — existing code imports ShippingZone from
# bosta.governorates. Keep the name available; new code should
# import LogisticsZone.
ShippingZone = LogisticsZone


@dataclass(frozen=True)
class Governorate:
    """Canonical governorate record.

    `code` is the wire identifier (ISO 3166-2). `bosta_code` is the
    legacy Bosta 3-letter identifier kept for back-compat with existing
    integrations and tests.
    """

    code: str  # ISO 3166-2, e.g. "EG-C"
    bosta_code: str  # legacy Bosta identifier, e.g. "CAI"
    name_en: str
    name_ar: str
    zone: LogisticsZone
    capital_en: str
    capital_ar: str


# All 27 Egyptian governorates. Order groups by logistics zone for
# readability — not a display order; consumers sort as needed.
EGYPTIAN_GOVERNORATES: tuple[Governorate, ...] = (
    # Greater Cairo
    Governorate(
        "EG-C",
        "CAI",
        "Cairo",
        "القاهرة",
        LogisticsZone.GREATER_CAIRO,
        "Cairo",
        "القاهرة",
    ),
    Governorate(
        "EG-GZ", "GIZ", "Giza", "الجيزة", LogisticsZone.GREATER_CAIRO, "Giza", "الجيزة"
    ),
    Governorate(
        "EG-KB",
        "QLY",
        "Qalyubia",
        "القليوبية",
        LogisticsZone.GREATER_CAIRO,
        "Banha",
        "بنها",
    ),
    # Alexandria & Delta
    Governorate(
        "EG-ALX",
        "ALX",
        "Alexandria",
        "الإسكندرية",
        LogisticsZone.DELTA,
        "Alexandria",
        "الإسكندرية",
    ),
    Governorate(
        "EG-BH", "BHR", "Beheira", "البحيرة", LogisticsZone.DELTA, "Damanhur", "دمنهور"
    ),
    Governorate(
        "EG-GH", "GHR", "Gharbia", "الغربية", LogisticsZone.DELTA, "Tanta", "طنطا"
    ),
    Governorate(
        "EG-MNF",
        "MNF",
        "Monufia",
        "المنوفية",
        LogisticsZone.DELTA,
        "Shebin El Kom",
        "شبين الكوم",
    ),
    Governorate(
        "EG-DK",
        "DKH",
        "Dakahlia",
        "الدقهلية",
        LogisticsZone.DELTA,
        "Mansoura",
        "المنصورة",
    ),
    Governorate(
        "EG-DT", "DMT", "Damietta", "دمياط", LogisticsZone.DELTA, "Damietta", "دمياط"
    ),
    Governorate(
        "EG-SHR",
        "SHR",
        "Sharqia",
        "الشرقية",
        LogisticsZone.DELTA,
        "Zagazig",
        "الزقازيق",
    ),
    Governorate(
        "EG-KFS",
        "KFS",
        "Kafr El Sheikh",
        "كفر الشيخ",
        LogisticsZone.DELTA,
        "Kafr El Sheikh",
        "كفر الشيخ",
    ),
    # Canal Zone & Sinai
    Governorate(
        "EG-PTS",
        "PSD",
        "Port Said",
        "بورسعيد",
        LogisticsZone.CANAL_SINAI,
        "Port Said",
        "بورسعيد",
    ),
    Governorate(
        "EG-IS",
        "ISM",
        "Ismailia",
        "الإسماعيلية",
        LogisticsZone.CANAL_SINAI,
        "Ismailia",
        "الإسماعيلية",
    ),
    Governorate(
        "EG-SUZ", "SUZ", "Suez", "السويس", LogisticsZone.CANAL_SINAI, "Suez", "السويس"
    ),
    Governorate(
        "EG-SIN",
        "NSN",
        "North Sinai",
        "شمال سيناء",
        LogisticsZone.CANAL_SINAI,
        "Arish",
        "العريش",
    ),
    Governorate(
        "EG-JS",
        "SSN",
        "South Sinai",
        "جنوب سيناء",
        LogisticsZone.CANAL_SINAI,
        "El Tor",
        "الطور",
    ),
    # Upper Egypt
    Governorate(
        "EG-FYM",
        "FYM",
        "Fayoum",
        "الفيوم",
        LogisticsZone.UPPER_EGYPT,
        "Fayoum",
        "الفيوم",
    ),
    Governorate(
        "EG-BNS",
        "BNS",
        "Beni Suef",
        "بني سويف",
        LogisticsZone.UPPER_EGYPT,
        "Beni Suef",
        "بني سويف",
    ),
    Governorate(
        "EG-MN", "MNY", "Minya", "المنيا", LogisticsZone.UPPER_EGYPT, "Minya", "المنيا"
    ),
    Governorate(
        "EG-AST", "AST", "Asyut", "أسيوط", LogisticsZone.UPPER_EGYPT, "Asyut", "أسيوط"
    ),
    Governorate(
        "EG-SHG", "SHG", "Sohag", "سوهاج", LogisticsZone.UPPER_EGYPT, "Sohag", "سوهاج"
    ),
    Governorate(
        "EG-KN", "QNA", "Qena", "قنا", LogisticsZone.UPPER_EGYPT, "Qena", "قنا"
    ),
    Governorate(
        "EG-LX", "LXR", "Luxor", "الأقصر", LogisticsZone.UPPER_EGYPT, "Luxor", "الأقصر"
    ),
    Governorate(
        "EG-ASN", "ASN", "Aswan", "أسوان", LogisticsZone.UPPER_EGYPT, "Aswan", "أسوان"
    ),
    # Remote
    Governorate(
        "EG-BA",
        "RDS",
        "Red Sea",
        "البحر الأحمر",
        LogisticsZone.REMOTE,
        "Hurghada",
        "الغردقة",
    ),
    Governorate(
        "EG-MT",
        "MTR",
        "Matrouh",
        "مطروح",
        LogisticsZone.REMOTE,
        "Marsa Matrouh",
        "مرسى مطروح",
    ),
    Governorate(
        "EG-WAD",
        "NWV",
        "New Valley",
        "الوادي الجديد",
        LogisticsZone.REMOTE,
        "Kharga",
        "الخارجة",
    ),
)

# Indices for O(1) lookup. Built once at module load.
_BY_ISO_CODE: dict[str, Governorate] = {g.code: g for g in EGYPTIAN_GOVERNORATES}
_BY_BOSTA_CODE: dict[str, Governorate] = {
    g.bosta_code: g for g in EGYPTIAN_GOVERNORATES
}
_BY_NAME_EN: dict[str, Governorate] = {
    g.name_en.lower(): g for g in EGYPTIAN_GOVERNORATES
}
_BY_NAME_AR: dict[str, Governorate] = {g.name_ar: g for g in EGYPTIAN_GOVERNORATES}

# Extra name aliases for fuzzy matching during legacy data migration.
# Keys should be .lower().strip() before lookup.
_NAME_ALIASES: dict[str, str] = {
    # English variants / misspellings observed in existing merchant data.
    "al qahira": "Cairo",
    "el qahira": "Cairo",
    "al-qahira": "Cairo",
    "cairo governorate": "Cairo",
    "el gize": "Giza",
    "al giza": "Giza",
    "al-giza": "Giza",
    "ad daqahliyah": "Dakahlia",
    "el mansoura": "Dakahlia",
    "mansoura": "Dakahlia",
    "el daqahliyah": "Dakahlia",
    "al gharbiyah": "Gharbia",
    "el gharbia": "Gharbia",
    "tanta": "Gharbia",
    "al minufiyah": "Monufia",
    "el monufia": "Monufia",
    "al qalyubiyah": "Qalyubia",
    "el qalyubia": "Qalyubia",
    "kalyobia": "Qalyubia",
    "banha": "Qalyubia",
    "al sharqiyah": "Sharqia",
    "el sharqia": "Sharqia",
    "al iskandariyah": "Alexandria",
    "el iskandariya": "Alexandria",
    "alex": "Alexandria",
    "el alexandria": "Alexandria",
    "al buhayrah": "Beheira",
    "el beheira": "Beheira",
    "damanhur": "Beheira",
    "kafr elsheikh": "Kafr El Sheikh",
    "kafr al sheikh": "Kafr El Sheikh",
    "kafr-el-sheikh": "Kafr El Sheikh",
    "dumyat": "Damietta",
    "el damietta": "Damietta",
    "bur said": "Port Said",
    "bour said": "Port Said",
    "al ismailiyah": "Ismailia",
    "ismaïlia": "Ismailia",
    "al suways": "Suez",
    "shamal sina": "North Sinai",
    "sinai north": "North Sinai",
    "janub sina": "South Sinai",
    "sinai south": "South Sinai",
    "al fayyum": "Fayoum",
    "fayyoum": "Fayoum",
    "el fayoum": "Fayoum",
    "bani suwayf": "Beni Suef",
    "beni sweif": "Beni Suef",
    "el minya": "Minya",
    "al minya": "Minya",
    "asyout": "Asyut",
    "assiut": "Asyut",
    "souhag": "Sohag",
    "el sohag": "Sohag",
    "qina": "Qena",
    "el qena": "Qena",
    "al uqsur": "Luxor",
    "loxor": "Luxor",
    "aswān": "Aswan",
    "asswan": "Aswan",
    "al bahr al ahmar": "Red Sea",
    "hurghada": "Red Sea",
    "red-sea": "Red Sea",
    "marsa matruh": "Matrouh",
    "mersa matrouh": "Matrouh",
    "matruh": "Matrouh",
    "al wadi al jadid": "New Valley",
    "new-valley": "New Valley",
    "kharga": "New Valley",
    # Arabic variants without diacritics / spacing drift.
    "القاهره": "Cairo",
    "الاسكندرية": "Alexandria",
    "الاسكندريه": "Alexandria",
    "الجيزه": "Giza",
    "كفرالشيخ": "Kafr El Sheikh",
    "الدقهليه": "Dakahlia",
    "الشرقيه": "Sharqia",
    "البحيره": "Beheira",
}


def get_governorate_by_code(code: str) -> Governorate | None:
    """Resolve a governorate by code.

    Accepts either the ISO 3166-2 code ("EG-C") or the legacy Bosta
    code ("CAI"). Case-insensitive.

    Args:
        code: Governorate code (ISO-3166-2 or legacy Bosta identifier).

    Returns:
        Governorate, or None if not found.
    """
    if not code:
        return None
    normalized = code.strip().upper()
    if normalized in _BY_ISO_CODE:
        return _BY_ISO_CODE[normalized]
    return _BY_BOSTA_CODE.get(normalized)


def get_governorate_by_name(name: str) -> Governorate | None:
    """Resolve a governorate by English or Arabic name, with fuzzy fallback.

    Matching order:
        1. Arabic exact match
        2. English case-insensitive match
        3. Alias map (common misspellings, English transliterations)

    Args:
        name: Governorate name in English or Arabic.

    Returns:
        Governorate, or None if not found.
    """
    if not name:
        return None
    raw = name.strip()
    # Arabic exact match (Arabic is case-insensitive by Unicode rules).
    if raw in _BY_NAME_AR:
        return _BY_NAME_AR[raw]
    lowered = raw.lower()
    if lowered in _BY_NAME_EN:
        return _BY_NAME_EN[lowered]
    # Alias fallback.
    canonical = _NAME_ALIASES.get(lowered)
    if canonical:
        return _BY_NAME_EN.get(canonical.lower())
    return None


def get_governorates_by_zone(zone: LogisticsZone) -> list[Governorate]:
    """Return all governorates in the given logistics zone."""
    return [g for g in EGYPTIAN_GOVERNORATES if g.zone == zone]


def get_all_governorates_dict(locale: str = "en") -> dict[str, str]:
    """Return all governorates as a `bosta_code → name` dict.

    Kept in this shape for back-compat with existing consumers that
    key on the Bosta 3-letter code. New code should iterate
    `EGYPTIAN_GOVERNORATES` directly.

    Args:
        locale: "en" or "ar".

    Returns:
        Dict mapping legacy Bosta code to name.
    """
    if locale == "ar":
        return {g.bosta_code: g.name_ar for g in EGYPTIAN_GOVERNORATES}
    return {g.bosta_code: g.name_en for g in EGYPTIAN_GOVERNORATES}


def resolve_governorate(token: str) -> Governorate | None:
    """Best-effort resolution used by the legacy-data migration.

    Tries, in order: ISO code, Bosta code, English/Arabic name, alias map.
    This is the single function the migration runs over every token in
    existing `store.settings.shipping.zones[].governorates` strings.
    """
    if not token:
        return None
    hit = get_governorate_by_code(token)
    if hit:
        return hit
    return get_governorate_by_name(token)

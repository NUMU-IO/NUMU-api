"""Free-form country name → ISO 3166-1 alpha-2 mapper.

The ``customer_addresses.country`` column is free-form, so the same
country lands in the database as ``"Egypt"``, ``"EG"``, ``"egypt"``,
or ``"مصر"`` depending on which form the storefront input collected.
Meta's CAPI ``user_data.country`` field, however, indexes hashes of
the lowercase 2-letter ISO code (``"eg"``). Sending the hash of any
other form produces a hash that never matches Meta's audience index —
worse than omitting the field, because a missing field is just a
lower EMQ score while a wrong hash is silently dropped.

This module canonicalizes to lowercase ISO-2. MENA coverage is the
priority (English + Arabic + common transliterations); a smaller
global table catches the rest. Anything that doesn't match returns
``None`` — caller should drop the field entirely rather than ship a
non-canonical hash.
"""

from __future__ import annotations

# MENA region first — these are the names Egyptian merchants' customers
# most commonly type, in English, Arabic, and common transliterations.
_MENA_COUNTRIES: dict[str, str] = {
    # Egypt
    "egypt": "eg",
    "eg": "eg",
    "egy": "eg",
    "مصر": "eg",
    # Saudi Arabia
    "saudi arabia": "sa",
    "saudi": "sa",
    "ksa": "sa",
    "sa": "sa",
    "sau": "sa",
    "kingdom of saudi arabia": "sa",
    "السعودية": "sa",
    "المملكة العربية السعودية": "sa",
    # United Arab Emirates
    "united arab emirates": "ae",
    "uae": "ae",
    "ae": "ae",
    "are": "ae",
    "emirates": "ae",
    "الإمارات": "ae",
    "الإمارات العربية المتحدة": "ae",
    # Kuwait
    "kuwait": "kw",
    "kw": "kw",
    "kwt": "kw",
    "الكويت": "kw",
    # Qatar
    "qatar": "qa",
    "qa": "qa",
    "qat": "qa",
    "قطر": "qa",
    # Bahrain
    "bahrain": "bh",
    "bh": "bh",
    "bhr": "bh",
    "البحرين": "bh",
    # Oman
    "oman": "om",
    "om": "om",
    "omn": "om",
    "عمان": "om",
    "سلطنة عمان": "om",
    # Jordan
    "jordan": "jo",
    "jo": "jo",
    "jor": "jo",
    "الأردن": "jo",
    # Lebanon
    "lebanon": "lb",
    "lb": "lb",
    "lbn": "lb",
    "لبنان": "lb",
    # Iraq
    "iraq": "iq",
    "iq": "iq",
    "irq": "iq",
    "العراق": "iq",
    # Syria
    "syria": "sy",
    "sy": "sy",
    "syr": "sy",
    "سوريا": "sy",
    "سورية": "sy",
    # Yemen
    "yemen": "ye",
    "ye": "ye",
    "yem": "ye",
    "اليمن": "ye",
    # Palestine
    "palestine": "ps",
    "ps": "ps",
    "pse": "ps",
    "فلسطين": "ps",
    # Libya
    "libya": "ly",
    "ly": "ly",
    "lby": "ly",
    "ليبيا": "ly",
    # Tunisia
    "tunisia": "tn",
    "tn": "tn",
    "tun": "tn",
    "تونس": "tn",
    # Morocco
    "morocco": "ma",
    "ma": "ma",
    "mar": "ma",
    "المغرب": "ma",
    # Algeria
    "algeria": "dz",
    "dz": "dz",
    "dza": "dz",
    "الجزائر": "dz",
    # Sudan
    "sudan": "sd",
    "sd": "sd",
    "sdn": "sd",
    "السودان": "sd",
}


# Broader catch-all for the non-MENA destinations Egyptian e-commerce
# stores ship to occasionally. Not exhaustive — only the names common
# enough to be worth hashing. Anything else returns None.
_OTHER_COUNTRIES: dict[str, str] = {
    "united states": "us",
    "usa": "us",
    "us": "us",
    "united states of america": "us",
    "united kingdom": "gb",
    "uk": "gb",
    "gb": "gb",
    "britain": "gb",
    "great britain": "gb",
    "canada": "ca",
    "ca": "ca",
    "australia": "au",
    "au": "au",
    "germany": "de",
    "de": "de",
    "deutschland": "de",
    "france": "fr",
    "fr": "fr",
    "italy": "it",
    "it": "it",
    "spain": "es",
    "es": "es",
    "netherlands": "nl",
    "nl": "nl",
    "holland": "nl",
    "switzerland": "ch",
    "ch": "ch",
    "sweden": "se",
    "se": "se",
    "norway": "no",
    "no": "no",
    "denmark": "dk",
    "dk": "dk",
    "turkey": "tr",
    "tr": "tr",
    "türkiye": "tr",
    "russia": "ru",
    "ru": "ru",
    "china": "cn",
    "cn": "cn",
    "india": "in",
    "in": "in",
    "pakistan": "pk",
    "pk": "pk",
    "south africa": "za",
    "za": "za",
    "nigeria": "ng",
    "ng": "ng",
    "kenya": "ke",
    "ke": "ke",
    "ethiopia": "et",
    "et": "et",
}


def canonicalize_country(value: str | None) -> str | None:
    """Return the lowercase ISO-2 code for ``value`` or ``None``.

    ``None`` signals the caller to drop the field — sending a hash of
    a non-canonical string actively hurts EMQ vs leaving the field
    empty. The mapping is case-insensitive and trims whitespace.
    """
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _MENA_COUNTRIES:
        return _MENA_COUNTRIES[normalized]
    if normalized in _OTHER_COUNTRIES:
        return _OTHER_COUNTRIES[normalized]
    return None

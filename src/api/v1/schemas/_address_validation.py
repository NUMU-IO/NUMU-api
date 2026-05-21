"""Shared address-format validators used by order + customer address DTOs.

Lives in `_address_validation.py` (underscore-prefixed) because the
helpers here are an implementation detail of the schema layer — not
something to import from routes or services. Routes should rely on
the validators having already run by the time the request DTO arrives.

v1 scope:
    - Country normalization to ISO 3166-1 alpha-2.
    - Postal-code regex per country (only the high-volume ones).
    - E.164-ish phone format check.

Geocoding (Google Places address verification) is intentionally out of
scope here — that's a Phase 2 enhancement that happens at a different
layer (post-validation, network-bound). Keeping these checks pure-Python
+ regex means they cost nothing per request and never block on a third
party.
"""

import re

# ISO 3166-1 alpha-2 codes the storefront actively accepts. Sourced
# from the country-name map used by the merchant hub's address form.
# We keep this short rather than the full 250+ list because the v1
# storefront only ships in MENA + a handful of high-traffic markets.
# Adding a country is a one-line change here.
ISO2_COUNTRIES: set[str] = {
    "EG",
    "AE",
    "SA",
    "JO",
    "QA",
    "KW",
    "BH",
    "OM",
    "LB",
    "MA",
    "TN",
    "DZ",
    "LY",
    "IQ",
    "PS",
    "YE",
    "SD",
    "US",
    "GB",
    "DE",
    "FR",
    "ES",
    "IT",
    "NL",
    "TR",
    "CN",
    "IN",
    "PK",
    "JP",
    "AU",
    "CA",
    "BR",
}

# Common country-name → ISO2 aliases. Storefront forms rarely surface
# the dropdown ISO code directly; we accept the most common spellings
# (English + a few Arabic transliterations) so legacy payloads keep
# working. Keys are compared case-insensitively.
COUNTRY_ALIASES: dict[str, str] = {
    "egypt": "EG",
    "مصر": "EG",
    "united arab emirates": "AE",
    "uae": "AE",
    "saudi arabia": "SA",
    "ksa": "SA",
    "jordan": "JO",
    "qatar": "QA",
    "kuwait": "KW",
    "bahrain": "BH",
    "oman": "OM",
    "lebanon": "LB",
    "morocco": "MA",
    "tunisia": "TN",
    "algeria": "DZ",
    "libya": "LY",
    "iraq": "IQ",
    "palestine": "PS",
    "yemen": "YE",
    "sudan": "SD",
    "united states": "US",
    "usa": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "germany": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "turkey": "TR",
    "china": "CN",
    "india": "IN",
    "pakistan": "PK",
    "japan": "JP",
    "australia": "AU",
    "canada": "CA",
    "brazil": "BR",
}

# Postal-code patterns per ISO2. Markets without a regex here pass
# any non-empty string through — we don't want to over-reject on
# countries where postal codes aren't strictly enforced.
POSTAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "EG": re.compile(r"^\d{5}$"),
    "US": re.compile(r"^\d{5}(-\d{4})?$"),
    "GB": re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE),
    "DE": re.compile(r"^\d{5}$"),
    "FR": re.compile(r"^\d{5}$"),
    "CA": re.compile(r"^[A-Z]\d[A-Z]\s*\d[A-Z]\d$", re.IGNORECASE),
    "AE": re.compile(r"^\d{4,6}$"),
    "SA": re.compile(r"^\d{5}$"),
}

# E.164-ish: optional leading +, 8–15 digits. Allows storefront forms
# to send "+201234567890" or "01234567890". The phone is rendered back
# to the customer + used for COD callbacks; we don't dial programmatically
# from this layer so a tighter parse isn't required.
PHONE_PATTERN: re.Pattern[str] = re.compile(r"^\+?\d{8,15}$")


def normalize_country(raw: str) -> str:
    """Normalize an inbound country to ISO 3166-1 alpha-2.

    Raises ValueError if the country isn't recognized.
    """
    cleaned = raw.strip()
    if len(cleaned) == 2 and cleaned.isalpha():
        code = cleaned.upper()
        if code in ISO2_COUNTRIES:
            return code
        raise ValueError(
            f"Unsupported country code '{cleaned}'. Use a valid ISO 3166-1 "
            f"alpha-2 country code."
        )
    alias = COUNTRY_ALIASES.get(cleaned.lower())
    if alias:
        return alias
    raise ValueError(
        f"Could not resolve country '{raw}'. Send an ISO 3166-1 alpha-2 "
        f"code (e.g. 'EG', 'US') or one of the supported country names."
    )


def normalize_phone(raw: str | None) -> str | None:
    """Strip separators and validate against the E.164-ish pattern.

    Returns None for empty input; raises ValueError on a malformed
    non-empty value.
    """
    if raw is None:
        return None
    cleaned = re.sub(r"[\s\-()]", "", raw.strip())
    if not cleaned:
        return None
    if not PHONE_PATTERN.match(cleaned):
        raise ValueError(
            "Phone must be 8–15 digits, optionally starting with '+'. "
            "Examples: '+201234567890', '01234567890'."
        )
    return cleaned


def check_postal_for_country(country: str, postal_code: str) -> None:
    """Raise ValueError if the postal code doesn't match the country pattern.

    No-op when the country isn't in `POSTAL_PATTERNS`. Caller is
    responsible for the empty/None case.
    """
    pattern = POSTAL_PATTERNS.get(country)
    if pattern is None:
        return
    if not pattern.match(postal_code):
        raise ValueError(
            f"Postal code '{postal_code}' isn't valid for country '{country}'."
        )

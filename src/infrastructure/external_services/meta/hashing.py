"""PII hashing helpers for Meta Conversions API.

Meta requires user_data PII fields (``em``, ``ph``, ``fn``, ``ln``,
``ct``, ``st``, ``zp``, ``country``, ``db``, ``external_id``) to be
SHA-256 hashed lowercase-trimmed before transmission. ``fbp``, ``fbc``,
``client_ip_address`` and ``client_user_agent`` are passed verbatim
per Meta's spec.

This module is the single source of truth — any caller hashing PII for
Meta MUST go through ``hash_user_data()``. Direct ``hashlib.sha256``
calls scattered across the codebase are a footgun (case-sensitivity,
trim, phone-format inconsistencies all silently destroy match quality).

**Wave 2 Phase 14 additions (2026-05-17):**

Egyptian merchants whose customers fill checkout in Arabic script were
losing match quality because Meta's audiences are keyed on the Latin
form of names. We now:

  1. Detect Arabic script in ``fn``/``ln``/``ct`` and emit BOTH the
     Latin-transliterated hash AND the Arabic-script hash as a 2-element
     list. Meta's spec accepts multi-value AM fields as alternatives, so
     the conversion matches whichever variant the merchant's audience
     was built against.

  2. Normalize phones across MENA (Egypt +20, Saudi +966, UAE +971,
     Morocco +212, Algeria +213), not just Egypt — same canonical
     E.164-without-plus shape that already worked for EG.

Implementation matches plan §5.6 + Wave 2 Phase 14.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Arabic Unicode ranges: main block + supplement + presentation forms.
# Detection is "any char in these ranges" — covers Egyptian + Levantine +
# Gulf dialect spellings + decorative ligatures.
_ARABIC_RANGES = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)

# Tashkil (diacritics) + tatweel — strip before transliteration so
# "مُحَمَّد" and "محمد" produce the same Latin output.
_ARABIC_DIACRITICS = re.compile(r"[ً-ٰٟـ]")

# Letter-by-letter Egyptian-dialect transliteration. ج→g (Egyptian),
# not the Levantine j. Used as a fallback for names not in the static
# map; the static map (transliteration_map_ar_eg.json) handles the
# common cases with canonical spellings.
_LETTER_MAP: dict[str, str] = {
    "ا": "a",
    "أ": "a",
    "إ": "e",
    "آ": "aa",
    "ٱ": "a",
    "ب": "b",
    "ت": "t",
    "ث": "th",
    "ج": "g",  # Egyptian dialect — Levantine would be "j"
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ذ": "th",
    "ر": "r",
    "ز": "z",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "d",
    "ط": "t",
    "ظ": "z",
    "ع": "a",
    "غ": "gh",
    "ف": "f",
    "ق": "q",
    "ك": "k",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ه": "h",
    "و": "w",
    "ي": "y",
    "ى": "a",
    "ء": "",
    "ؤ": "o",
    "ئ": "e",
    "ة": "a",
    # Arabic-Indic digits — for the rare case a name field contains digits.
    "٠": "0",
    "١": "1",
    "٢": "2",
    "٣": "3",
    "٤": "4",
    "٥": "5",
    "٦": "6",
    "٧": "7",
    "٨": "8",
    "٩": "9",
}

# Country phone prefixes — keyed by the canonical E.164 country code.
# Order matters: 966 must be checked before 6 (subscriber), etc. We
# use longest-prefix matching at call time.
_MENA_COUNTRY_CODES: tuple[str, ...] = ("966", "971", "212", "213", "20")

# Arabic-Indic + Eastern Arabic-Indic digit normalization. Without this,
# a phone entered in Arabic-script digits hashes to a different SHA-256
# than the same phone entered in ASCII — silently destroys browser/CAPI
# dedup. The pre-Phase-14 implementation passed isdigit() but didn't
# translate, so the bug existed but was undetected.
_DIGIT_TRANSLATE = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",  # Arabic-Indic + Eastern Arabic-Indic
    "01234567890123456789",
)

_TRANSLITERATION_MAP_PATH = Path(__file__).parent / "transliteration_map_ar_eg.json"


# ---------------------------------------------------------------------------
# Internal helpers — character class detection + transliteration
# ---------------------------------------------------------------------------


def _is_arabic_script(s: str | None) -> bool:
    """True if any code point in ``s`` is in an Arabic Unicode block.

    Mixed-script strings ("Mohamed محمد") return True — we still want
    to emit a Latin variant for the Arabic portion alongside the
    original-form hash.
    """
    if not s:
        return False
    return any(any(lo <= ord(c) <= hi for lo, hi in _ARABIC_RANGES) for c in s)


@lru_cache(maxsize=1)
def _load_transliteration_map() -> dict[str, dict[str, str]]:
    """Load + cache the static Arabic→Latin name/city map.

    Falls back to an empty dict if the file is missing or malformed —
    in that case ``_transliterate_arabic_to_latin`` relies entirely on
    the letter-by-letter map. The function MUST NOT raise: a missing
    transliteration map should degrade gracefully (lower EMQ), never
    break a CAPI fire.
    """
    try:
        raw = json.loads(_TRANSLITERATION_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"first_names": {}, "last_names": {}, "cities": {}}
    return {
        "first_names": raw.get("first_names") or {},
        "last_names": raw.get("last_names") or {},
        "cities": raw.get("cities") or {},
    }


def _strip_diacritics(s: str) -> str:
    """Remove Arabic tashkil + tatweel so map lookups are stable.

    ``مُحَمَّد`` → ``محمد`` so both forms hit the same static-map entry.
    """
    return _ARABIC_DIACRITICS.sub("", s)


def _transliterate_arabic_to_latin(s: str, *, field: str | None = None) -> str:
    """Convert Arabic-script input to Latin script.

    Strategy:
      1. Strip Arabic diacritics + tatweel.
      2. Look up the exact normalized string in the appropriate map
         section (first_names / last_names / cities) — these carry the
         canonical Egyptian-merchant Latin spellings.
      3. Fall back to letter-by-letter transliteration via
         ``_LETTER_MAP``.

    ``field`` selects which map section to consult; pass ``"fn"``,
    ``"ln"``, or ``"ct"``. ``None`` skips the static-map lookup and
    goes straight to letter-by-letter (used by the catch-all
    transliterator in tests).
    """
    cleaned = _strip_diacritics(s.strip()).lower()
    if not cleaned:
        return ""

    if field is not None:
        sections = _load_transliteration_map()
        section_key = {
            "fn": "first_names",
            "ln": "last_names",
            "ct": "cities",
        }.get(field)
        if section_key:
            mapped = sections.get(section_key, {}).get(cleaned)
            if mapped:
                return mapped

    # Letter-by-letter fallback — handles names not in the static map.
    # Unknown characters (Latin, digits, punctuation) pass through.
    out_chars: list[str] = []
    for ch in cleaned:
        out_chars.append(_LETTER_MAP.get(ch, ch))
    return "".join(out_chars)


def _normalize_name(s: str | None, *, field: str) -> list[str] | None:
    """Return the list of normalized variants to hash for a name field.

    Outputs:
      * ``None`` if input is empty / None — caller drops the field.
      * 1-element list ``[latin]`` if input is pure Latin script
        (no Arabic detected) — backward-compatible.
      * 2-element list ``[latin, arabic_original]`` if input contains
        Arabic script — Meta accepts multi-value AM fields as
        alternatives, so whichever form the merchant's audience holds
        matches the conversion.

    The returned strings are NOT hashed yet — caller pipes each through
    ``_h`` to produce the SHA-256 digests.
    """
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    if not _is_arabic_script(s):
        return [s]
    # Arabic-only or mixed-script input: emit both transliterations.
    latin = _transliterate_arabic_to_latin(s, field=field)
    arabic_clean = _strip_diacritics(s).lower()
    # Dedup: a name that's already in the static map and matches its
    # canonical Latin form on letter-by-letter shouldn't produce two
    # identical hashes.
    variants = [v for v in (latin, arabic_clean) if v]
    seen: set[str] = set()
    deduped: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped or None


# ---------------------------------------------------------------------------
# SHA-256 primitive
# ---------------------------------------------------------------------------


def _h(s: str | None) -> str | None:
    """SHA-256 the lowercase-trimmed UTF-8 bytes of ``s``.

    Returns None when ``s`` is None or empty so callers can skip the
    field entirely (Meta drops nulls server-side, but sending None
    keys is wasteful and slightly degrades match quality scoring).
    """
    if not s:
        return None
    return hashlib.sha256(s.strip().lower().encode()).hexdigest()


def _h_each(values: list[str] | None) -> list[str] | None:
    """SHA-256 each value in ``values``. Drops empty results.

    Used by ``hash_user_data`` for name/city fields where
    ``_normalize_name`` may return 1 or 2 variants.
    """
    if not values:
        return None
    hashed = [_h(v) for v in values]
    hashed = [h for h in hashed if h]
    return hashed or None


# ---------------------------------------------------------------------------
# Phone normalization (MENA)
# ---------------------------------------------------------------------------


def _normalize_mena_phone(phone: str) -> str:
    """Normalize a MENA mobile number to E.164-without-plus form.

    Accepts any of:
        +201001234567   (E.164 with +)
        201001234567    (E.164 without +)
        01001234567     (Egyptian national format)
        ٠١٠٠١٢٣٤٥٦٧     (Arabic-Indic digits — handled via isdigit())
        +966501234567   (Saudi E.164)
        0501234567      (Saudi/UAE national format — assumed Saudi by default,
                         but per-country detection requires the +CC prefix)
        +971501234567   (UAE E.164)
        +212661234567   (Morocco E.164)
        +213551234567   (Algeria E.164)

    Returns the country code + subscriber digits (no +). Egypt is the
    default for national-format numbers without an explicit prefix —
    matches the historical ``_normalize_eg_phone`` behavior.

    Always returning the same canonical shape is what makes the
    SHA-256 hash match across browser-side Pixel and server-side CAPI.
    """
    if not phone:
        return ""
    # Translate any Arabic-Indic digits to ASCII before extracting — a
    # phone entered as "٠١٠٠١٢٣٤٥٦٧" must hash identically to the
    # ASCII form "01001234567".
    phone_ascii = phone.translate(_DIGIT_TRANSLATE)
    digits = "".join(c for c in phone_ascii if c.isdigit())
    if not digits:
        return ""

    # Longest-prefix country-code match. 966/971/212/213 are 3-digit;
    # 20 is 2-digit. Try 3-digit first so "20" doesn't shadow "212".
    for cc in sorted(_MENA_COUNTRY_CODES, key=len, reverse=True):
        if digits.startswith(cc):
            # Strip the prefix, then strip any leading 0 from the
            # national segment (rare but happens — "200109..." vs
            # "2001001..."). The canonical form is ``CC + subscriber``.
            subscriber = digits[len(cc) :]
            if subscriber.startswith("0"):
                subscriber = subscriber[1:]
            return cc + subscriber

    # No country prefix → assume Egypt + strip a leading 0 (backward
    # compatible with the legacy ``_normalize_eg_phone`` contract).
    if digits.startswith("0"):
        digits = digits[1:]
    return "20" + digits


def _normalize_eg_phone(phone: str) -> str:
    """Backward-compat alias for the original Egyptian-only normalizer.

    Existing tests + callers still reference ``_normalize_eg_phone``;
    this thin wrapper delegates to ``_normalize_mena_phone`` so the
    behavior is unchanged for Egyptian inputs and gains MENA support
    for everything else.
    """
    return _normalize_mena_phone(phone)


# ---------------------------------------------------------------------------
# Public API — used by the Celery CAPI task
# ---------------------------------------------------------------------------


def hash_user_data(raw: dict) -> dict:
    """Convert a raw user-data dict into Meta's hashed CAPI shape.

    Input keys are NUMU's internal vocabulary (``email``, ``phone``,
    ``first_name``, ``city`` …); output keys match Meta's CAPI spec
    (``em``, ``ph``, ``fn``, ``ct`` …) with values wrapped in lists
    per Meta's hashed-field contract.

    Fields not in ``raw`` are emitted as ``None`` (Meta tolerates and
    drops them server-side); this keeps the payload shape stable for
    ``meta_event_log.request_payload`` redaction logic.

    **Wave 2 Phase 14 behavior change.** ``fn``/``ln``/``ct`` now emit
    a 2-element list ``[hash(latin), hash(arabic_form)]`` when the
    input is in Arabic script — lifts match quality for Egyptian
    merchants whose audiences hold the Latin form of customer names.
    Pure-Latin inputs continue to emit a 1-element list (no behavior
    change for non-MENA stores).
    """
    return {
        "em": [_h(raw["email"])] if raw.get("email") else None,
        "ph": [_h(_normalize_mena_phone(raw["phone"]))] if raw.get("phone") else None,
        "fn": _h_each(_normalize_name(raw.get("first_name"), field="fn")),
        "ln": _h_each(_normalize_name(raw.get("last_name"), field="ln")),
        "ct": _h_each(_normalize_name(raw.get("city"), field="ct")),
        "country": [_h(raw["country_code"])] if raw.get("country_code") else None,
        "zp": [_h(raw["zip"])] if raw.get("zip") else None,
        # NOT hashed — Meta wants these raw:
        "fbp": raw.get("fbp"),
        "fbc": raw.get("fbc"),
        "client_ip_address": raw.get("ip"),
        "client_user_agent": raw.get("user_agent"),
        "external_id": [_h(raw["customer_id"])] if raw.get("customer_id") else None,
    }

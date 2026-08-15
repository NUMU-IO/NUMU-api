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

# Meta's field normalization (verified against the Customer Information
# Parameters doc, 2026-08-17):
#   fn / ln  "Lowercase only with no punctuation."
#   ct       "Lowercase only with no punctuation, no special characters,
#             and no spaces."
#   st       "…normalize states outside the U.S. in lowercase with no
#             punctuation, no special characters, and no spaces."
#   zp       "Use lowercase with no spaces and no dash."
#
# Before this, every one of these fields was hashed by `_h()`, which only
# trims and lowercases. So "New Cairo" hashed as `new cairo` while Meta
# indexes `newcairo`, and "Al-Sayed" hashed as `al-sayed` against Meta's
# `alsayed`. The field was present, the hash was well-formed, and it could
# never match — the most expensive kind of bug, because every diagnostic
# reports the parameter as covered.
#
# `[^\w\s]` is Unicode-aware in Python 3, so Arabic letters survive and
# Arabic combining marks (category Mn, which are not alnum) are removed —
# the same normalization `_strip_diacritics` already applies.
_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)


def _normalize_meta_text(s: str | None, *, strip_spaces: bool) -> str | None:
    """Normalize a text match key to the exact shape Meta indexes.

    Lowercases, removes punctuation and underscores, then either collapses
    internal whitespace to single spaces (``fn``/``ln``) or removes it
    entirely (``ct``/``st``). Returns None for anything that normalizes to
    empty, so the caller drops the field rather than hashing "".

    NOT used for ``em`` — an email must keep its ``@`` and ``.``, and Meta
    asks only for trim + lowercase there.
    """
    if not s:
        return None
    out = _PUNCT_RE.sub("", s)
    out = (
        _WHITESPACE_RE.sub("", out)
        if strip_spaces
        else _WHITESPACE_RE.sub(" ", out).strip()
    )
    return out.lower() or None


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


def _normalize_name(
    s: str | None, *, field: str, strip_spaces: bool = False
) -> list[str] | None:
    """Return the list of normalized variants to hash for a name-like field.

    Outputs:
      * ``None`` if input is empty / None — caller drops the field.
      * 1-element list ``[latin]`` if input is pure Latin script
        (no Arabic detected) — backward-compatible.
      * 2-element list ``[latin, arabic_original]`` if input contains
        Arabic script — Meta accepts multi-value AM fields as
        alternatives, so whichever form the merchant's audience holds
        matches the conversion.

    Every variant is put through ``_normalize_meta_text`` before it is
    returned, so the digest matches the form Meta actually indexes.
    ``strip_spaces=True`` for ``ct``/``st``, False for ``fn``/``ln`` —
    Meta specifies "no spaces" only for the former pair.

    The returned strings are NOT hashed yet — caller pipes each through
    ``_h`` to produce the SHA-256 digests.
    """
    if not s:
        return None
    s = s.strip()
    if not s:
        return None

    if not _is_arabic_script(s):
        normalized = _normalize_meta_text(s, strip_spaces=strip_spaces)
        return [normalized] if normalized else None

    # Arabic-only or mixed-script input: emit both transliterations.
    latin = _transliterate_arabic_to_latin(s, field=field)
    arabic_clean = _strip_diacritics(s).lower()
    # Dedup: a name that's already in the static map and matches its
    # canonical Latin form on letter-by-letter shouldn't produce two
    # identical hashes. Normalize FIRST so two spellings that differ only
    # by punctuation collapse to one digest instead of two.
    seen: set[str] = set()
    deduped: list[str] = []
    for raw in (latin, arabic_clean):
        v = _normalize_meta_text(raw, strip_spaces=strip_spaces)
        if v and v not in seen:
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
        # ct/st strip spaces as well as punctuation — Meta's spec differs
        # from fn/ln here, and "New Cairo" vs "newcairo" is the difference
        # between a match and a wasted parameter.
        "ct": _h_each(_normalize_name(raw.get("city"), field="ct", strip_spaces=True)),
        # State / governorate. Present on every Egyptian address we collect
        # and on OrderShippingAddress, and the transliteration map's `cities`
        # section already carries the governorate spellings — but until now
        # `st` was not even a key in this dict, so the data was collected
        # everywhere and sent nowhere. Reuses field="ct" deliberately: in
        # Egypt the governorate and the city share a vocabulary (Cairo,
        # Alexandria, Giza…), so the same static map resolves both.
        "st": _h_each(_normalize_name(raw.get("state"), field="ct", strip_spaces=True)),
        # Country is canonicalized HERE as well as by the callers. Meta only
        # indexes the hash of the lowercase ISO-2 code, so a free-form
        # "Egypt" would hash to something that matches nothing — and the
        # failure is invisible (no error, just a permanently unmatched
        # field). Every current caller canonicalizes first; this makes the
        # contract explicit instead of implicit, so a future caller passing
        # a raw address value cannot silently degrade match quality.
        "country": _country_hash(raw.get("country_code")),
        # Zip: strip ALL whitespace, not just the ends. Meta's spec is
        # lowercase with no spaces, so "SW1A 1AA" and "sw1a1aa" must not
        # produce two different digests. Egyptian postal codes are numeric
        # so this is mostly future-proofing for the Saudi/Gulf expansion.
        "zp": _zip_hash(raw.get("zip")),
        # NOT hashed — Meta wants these raw:
        "fbp": raw.get("fbp"),
        "fbc": raw.get("fbc"),
        "client_ip_address": raw.get("ip"),
        "client_user_agent": raw.get("user_agent"),
        "external_id": _external_ids(raw),
    }


def _country_hash(raw_country: str | None) -> list[str] | None:
    """Hash a country value, canonicalizing to lowercase ISO-3166-1 alpha-2.

    Unmappable values are DROPPED rather than hashed as-is: a digest of
    "united arab emirates" matches nothing in Meta's index, and sending a
    field that can never match is worse than sending no field — it counts
    against the event's customer-information completeness without ever
    contributing a match.
    """
    if not raw_country:
        return None
    from src.infrastructure.external_services.meta.country_iso import (
        canonicalize_country,
    )

    iso2 = canonicalize_country(raw_country)
    digest = _h(iso2) if iso2 else None
    return [digest] if digest else None


def _zip_hash(raw_zip: str | None) -> list[str] | None:
    """Hash a postal code — Meta: "lowercase with no spaces and no dash".

    The dash mattered and was missing: a Saudi/Gulf code entered as
    "12345-6789" hashed differently from "123456789". Egyptian codes are
    plain numerics so this was latent, but the Gulf expansion makes it live.
    """
    if not raw_zip:
        return None
    compact = re.sub(r"[\s\-‐-―]+", "", str(raw_zip))
    digest = _h(compact)
    return [digest] if digest else None


def _external_ids(raw: dict) -> list[str] | None:
    """Hashed ``external_id`` values — customer id first, session id second.

    Meta accepts ``external_id`` as an ARRAY of alternatives and will match on
    any element, so sending both costs one extra hash and can only help.

    Why two: this used to read ``customer_id`` alone, which meant a guest
    checkout — the majority of MENA orders — sent no ``external_id`` at all on
    any mid-funnel event. The session fingerprint fills that gap with a
    pseudonymous, already-hashed identifier, and because the SAME value is sent
    across every event in a session, Meta can stitch a guest's
    ViewContent → AddToCart → InitiateCheckout → Purchase into one person.

    For a logged-in visitor both are present and the customer id leads, so
    audiences built on customer ids keep matching exactly as before.
    """
    ids = [_h(str(raw[key])) for key in ("customer_id", "external_id") if raw.get(key)]
    # De-dupe while preserving order: when the same value arrives under both
    # keys, Meta should see one entry, not a repeat.
    seen: set[str] = set()
    unique = [i for i in ids if not (i in seen or seen.add(i))]
    return unique or None

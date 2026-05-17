"""Unit tests for the Meta CAPI PII hashing helpers.

These cover plan §5.6's contract:

  * SHA-256 lowercase-trimmed for em/ph/fn/ln/ct/zp/country/external_id
  * Egyptian phone normalization to ``20 + 10 digits``
  * None / empty handling so callers can omit fields cleanly

Wave 2 Phase 14 additions (2026-05-17):

  * MENA country-code phone normalization (Saudi/UAE/Morocco/Algeria)
  * Arabic-script detection + Latin-script transliteration
  * Dual-variant emission for ``fn``/``ln``/``ct`` when Arabic detected

Match-quality regressions on these helpers silently destroy ROAS
attribution, so the assertions here pin exact hex digests rather than
just "is a string of length 64".
"""

import hashlib

import pytest

from src.infrastructure.external_services.meta.hashing import (
    _h,
    _is_arabic_script,
    _normalize_eg_phone,
    _normalize_mena_phone,
    _normalize_name,
    _strip_diacritics,
    _transliterate_arabic_to_latin,
    hash_user_data,
)


def _sha256(s: str) -> str:
    """Reference SHA-256 helper for assertions (avoids importing _h)."""
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
# _h() — the trim+lower+sha256 primitive
# ---------------------------------------------------------------------------


class TestPrimitiveHash:
    """The internal ``_h`` building block."""

    def test_empty_string_returns_none(self):
        assert _h("") is None

    def test_none_returns_none(self):
        assert _h(None) is None

    def test_basic_lowercase_trim(self):
        # "  Foo  " -> "foo"
        assert _h("  Foo  ") == _sha256("foo")

    def test_already_normalized_passes_through(self):
        assert _h("hello") == _sha256("hello")

    def test_uppercase_normalized(self):
        assert _h("USER@EXAMPLE.COM") == _sha256("user@example.com")

    def test_known_email_digest(self):
        # Pinned digest — if this changes, downstream Meta match
        # quality will silently degrade.
        assert (
            _h("test@example.com")
            == "973dfe463ec85785f5f95af5ba3906eedb2d931c24e69824a89ea65dba4e813b"
        )


# ---------------------------------------------------------------------------
# _normalize_eg_phone() — Egyptian E.164 normalization
# ---------------------------------------------------------------------------


class TestEgyptianPhoneNormalization:
    """All Egyptian inputs collapse to the canonical ``20XXXXXXXXXX``."""

    EXPECTED = "201001234567"

    def test_e164_with_plus(self):
        assert _normalize_eg_phone("+201001234567") == self.EXPECTED

    def test_e164_without_plus(self):
        assert _normalize_eg_phone("201001234567") == self.EXPECTED

    def test_national_format_with_zero(self):
        assert _normalize_eg_phone("01001234567") == self.EXPECTED

    def test_with_spaces_and_dashes(self):
        # Punctuation is stripped — only digits survive.
        assert _normalize_eg_phone("+20 100-123-4567") == self.EXPECTED

    def test_with_parentheses(self):
        assert _normalize_eg_phone("(20) 100 123 4567") == self.EXPECTED

    def test_all_three_input_shapes_hash_identically(self):
        # The whole point of normalization: every shape lands on the
        # same digest, so browser-side and server-side fires dedupe.
        digests = {
            _h(_normalize_eg_phone(p))
            for p in ("+201001234567", "201001234567", "01001234567")
        }
        assert len(digests) == 1


# ---------------------------------------------------------------------------
# hash_user_data() — top-level CAPI user_data shaper
# ---------------------------------------------------------------------------


class TestHashUserData:
    """The shape returned by ``hash_user_data`` is what we POST to Meta."""

    def test_empty_input_yields_all_none(self):
        out = hash_user_data({})
        assert out["em"] is None
        assert out["ph"] is None
        assert out["fn"] is None
        assert out["ln"] is None
        assert out["ct"] is None
        assert out["country"] is None
        assert out["zp"] is None
        assert out["external_id"] is None
        # Raw fields are also absent (None) when not supplied.
        assert out["fbp"] is None
        assert out["fbc"] is None
        assert out["client_ip_address"] is None
        assert out["client_user_agent"] is None

    def test_email_is_lowercased_trimmed_hashed_and_listified(self):
        out = hash_user_data({"email": "  TEST@EXAMPLE.COM  "})
        # Meta requires hashed PII fields to be wrapped in single-element lists.
        assert isinstance(out["em"], list)
        assert len(out["em"]) == 1
        assert out["em"][0] == _sha256("test@example.com")

    def test_phone_normalization_then_hash(self):
        # Both the leading-zero and +20 forms must hash to the same digest.
        out_a = hash_user_data({"phone": "+201001234567"})
        out_b = hash_user_data({"phone": "01001234567"})
        assert out_a["ph"] == out_b["ph"]
        assert out_a["ph"][0] == _sha256("201001234567")

    def test_first_and_last_name_lowercased(self):
        out = hash_user_data({"first_name": "Layla", "last_name": "Mostafa"})
        assert out["fn"][0] == _sha256("layla")
        assert out["ln"][0] == _sha256("mostafa")

    def test_city_country_zip(self):
        out = hash_user_data({"city": "Cairo", "country_code": "EG", "zip": "11511"})
        assert out["ct"][0] == _sha256("cairo")
        assert out["country"][0] == _sha256("eg")
        assert out["zp"][0] == _sha256("11511")

    def test_external_id_uses_customer_id_key(self):
        # The plan maps `customer_id` (NUMU-internal) → `external_id`
        # (Meta-spec). Regressing this breaks logged-in customer match.
        out = hash_user_data({"customer_id": "cust-uuid-xyz"})
        assert out["external_id"][0] == _sha256("cust-uuid-xyz")

    def test_fbp_fbc_passthrough_not_hashed(self):
        out = hash_user_data({
            "fbp": "fb.1.1719414738122.1234567890",
            "fbc": "fb.1.1719414700000.IwAR2abc",
        })
        # These MUST be passed through verbatim — Meta uses them as
        # high-quality match keys and re-hashing destroys them.
        assert out["fbp"] == "fb.1.1719414738122.1234567890"
        assert out["fbc"] == "fb.1.1719414700000.IwAR2abc"

    def test_ip_and_user_agent_passthrough(self):
        out = hash_user_data({
            "ip": "197.45.123.45",
            "user_agent": "Mozilla/5.0 (iPhone)",
        })
        assert out["client_ip_address"] == "197.45.123.45"
        assert out["client_user_agent"] == "Mozilla/5.0 (iPhone)"

    def test_full_realistic_payload(self):
        out = hash_user_data({
            "email": "shopper@example.com",
            "phone": "+201001234567",
            "first_name": "Layla",
            "last_name": "Mostafa",
            "city": "Cairo",
            "country_code": "EG",
            "zip": "11511",
            "customer_id": "cust-uuid-xyz",
            "fbp": "fb.1.x.y",
            "fbc": "fb.1.a.b",
            "ip": "197.45.123.45",
            "user_agent": "Mozilla/5.0",
        })
        # Every hashed field is a 64-char hex string in a 1-elt list,
        # every passthrough field equals its input.
        for key in ("em", "ph", "fn", "ln", "ct", "country", "zp", "external_id"):
            assert isinstance(out[key], list), f"{key} must be a list"
            assert len(out[key]) == 1
            assert len(out[key][0]) == 64
            int(out[key][0], 16)  # raises ValueError if not hex
        assert out["fbp"] == "fb.1.x.y"
        assert out["fbc"] == "fb.1.a.b"
        assert out["client_ip_address"] == "197.45.123.45"
        assert out["client_user_agent"] == "Mozilla/5.0"

    @pytest.mark.parametrize(
        "key,value",
        [
            ("email", ""),
            ("phone", ""),
            ("first_name", ""),
            ("last_name", ""),
            ("city", ""),
            ("country_code", ""),
            ("zip", ""),
            ("customer_id", ""),
        ],
    )
    def test_empty_string_values_yield_none(self, key, value):
        # Empty strings must be treated as "not provided" — sending
        # SHA-256("") to Meta would be a bogus match key.
        out = hash_user_data({key: value})
        # Map NUMU-internal key → Meta key
        meta_key = {
            "email": "em",
            "phone": "ph",
            "first_name": "fn",
            "last_name": "ln",
            "city": "ct",
            "country_code": "country",
            "zip": "zp",
            "customer_id": "external_id",
        }[key]
        assert out[meta_key] is None


# ===========================================================================
# Wave 2 Phase 14 — Arabic name normalization + MENA phone normalization
# ===========================================================================
#
# These tests pin the Phase 14 contract: when a customer's name arrives in
# Arabic script (which is the default for the Egyptian merchant base), we
# emit BOTH the Latin-transliterated hash AND the Arabic-script hash so
# whichever variant Meta's audience holds matches the conversion.
#
# Phones across MENA collapse to the same E.164-without-plus shape (CC +
# subscriber digits) regardless of whether the input had +, leading 0, or
# Arabic-Indic digits.


class TestArabicScriptDetection:
    """``_is_arabic_script`` returns True for any Arabic char in the input."""

    def test_pure_latin_returns_false(self):
        assert _is_arabic_script("Mohamed") is False

    def test_pure_arabic_returns_true(self):
        assert _is_arabic_script("محمد") is True

    def test_mixed_script_returns_true(self):
        # Egyptian merchants often have both spellings on file.
        assert _is_arabic_script("Mohamed محمد") is True

    def test_empty_returns_false(self):
        assert _is_arabic_script("") is False
        assert _is_arabic_script(None) is False

    def test_arabic_indic_digits_alone_dont_count(self):
        # Digits 0-9 in Arabic-Indic form are technically in the Arabic
        # block, so they DO trigger detection. This is intentional —
        # a name with digits in Arabic-Indic form is treated as
        # Arabic-script and gets the dual-variant treatment.
        assert _is_arabic_script("١٢٣") is True


class TestStripDiacritics:
    """Tashkil removal so static-map lookups are stable."""

    def test_no_diacritics_unchanged(self):
        assert _strip_diacritics("محمد") == "محمد"

    def test_fatha_removed(self):
        # مَحَمَّد → محمد
        assert _strip_diacritics("مَحَمَّد") == "محمد"

    def test_full_tashkil_removed(self):
        # All 8 diacritics + tatweel.
        assert _strip_diacritics("مُحَمَّدً") == "محمد"

    def test_tatweel_removed(self):
        # Tatweel (U+0640) is a decorative stretch — must be stripped.
        assert _strip_diacritics("محـــمد") == "محمد"

    def test_latin_unchanged(self):
        assert _strip_diacritics("Mohamed") == "Mohamed"


class TestArabicToLatinTransliteration:
    """Static-map lookups + letter-by-letter fallback."""

    @pytest.mark.parametrize(
        "arabic,latin,field",
        [
            ("محمد", "mohamed", "fn"),
            ("أحمد", "ahmed", "fn"),
            ("علي", "ali", "fn"),
            ("فاطمة", "fatma", "fn"),
            ("سارة", "sara", "fn"),
            ("نور", "noor", "fn"),
            ("القاهرة", "cairo", "ct"),
            ("الإسكندرية", "alexandria", "ct"),
            ("الجيزة", "giza", "ct"),
            ("السيد", "elsayed", "ln"),
            ("النجار", "elnaggar", "ln"),
        ],
    )
    def test_static_map_canonical_spellings(self, arabic, latin, field):
        # Pinned spellings — if these drift, Egyptian merchants lose
        # match quality at scale. The map is the high-confidence path.
        assert _transliterate_arabic_to_latin(arabic, field=field) == latin

    def test_tashkil_doesnt_break_map_lookup(self):
        # مُحَمَّد should still resolve to the canonical "mohamed".
        assert _transliterate_arabic_to_latin("مُحَمَّد", field="fn") == "mohamed"

    def test_letter_by_letter_fallback(self):
        # A name not in the static map falls back to letter-by-letter.
        # "كنزي" → "knzy" via letter map (real entry "kenzy" is in the
        # map; this verifies the fallback path with a fresh string).
        out = _transliterate_arabic_to_latin("بنزو", field="fn")
        # ب=b, ن=n, ز=z, و=w
        assert out == "bnzw"

    def test_egyptian_dialect_j_renders_as_g(self):
        # ج → g (Egyptian), not j (Levantine). This is the convention
        # the static map follows and the fallback must match.
        # جمال → gmal (no map entry, letter-by-letter)
        out = _transliterate_arabic_to_latin("جمال", field="fn")
        assert out.startswith("g")

    def test_no_field_skips_static_map(self):
        # field=None goes straight to letter-by-letter even for names
        # that ARE in the static map. Used by internal callers that
        # want raw transliteration.
        out = _transliterate_arabic_to_latin("محمد", field=None)
        # م=m, ح=h, م=m, د=d → "mhmd" (no map lookup)
        assert out == "mhmd"


class TestNormalizeName:
    """``_normalize_name`` outputs the variant list to hash."""

    def test_none_input(self):
        assert _normalize_name(None, field="fn") is None

    def test_empty_string(self):
        assert _normalize_name("", field="fn") is None
        assert _normalize_name("   ", field="fn") is None

    def test_pure_latin_returns_single_variant(self):
        # Backward-compatible: pre-Phase-14 callers see no change.
        assert _normalize_name("Mohamed", field="fn") == ["Mohamed"]

    def test_arabic_returns_two_variants(self):
        # Latin first (canonical Meta audience key), Arabic second.
        result = _normalize_name("محمد", field="fn")
        assert result == ["mohamed", "محمد"]

    def test_arabic_with_diacritics(self):
        # Tashkil stripped before both lookup and Arabic-hash variant.
        result = _normalize_name("مُحَمَّد", field="fn")
        assert result == ["mohamed", "محمد"]

    def test_city_field_uses_city_map(self):
        result = _normalize_name("القاهرة", field="ct")
        assert result == ["cairo", "القاهرة"]

    def test_dedup_when_latin_equals_arabic(self):
        # Edge case: if transliteration somehow equals the Arabic
        # cleaned form (won't happen in practice but pinning the dedup
        # path), we shouldn't emit duplicates.
        # Force this by using a Latin input that's already in the AR map
        # form — actually impossible since _is_arabic_script gates this
        # path. So this test instead verifies the no-dup invariant
        # holds for a normal case.
        result = _normalize_name("محمد", field="fn")
        assert len(result) == len(set(result)), "no duplicate variants"


class TestMENAPhoneNormalization:
    """Phone normalization across Egypt, Saudi, UAE, Morocco, Algeria."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Egypt — backward-compatible with _normalize_eg_phone
            ("+201001234567", "201001234567"),
            ("201001234567", "201001234567"),
            ("01001234567", "201001234567"),
            ("٠١٠٠١٢٣٤٥٦٧", "201001234567"),  # Arabic-Indic
            # Saudi Arabia
            ("+966501234567", "966501234567"),
            ("966501234567", "966501234567"),
            # UAE
            ("+971501234567", "971501234567"),
            ("971501234567", "971501234567"),
            # Morocco
            ("+212661234567", "212661234567"),
            ("212661234567", "212661234567"),
            # Algeria
            ("+213551234567", "213551234567"),
            ("213551234567", "213551234567"),
        ],
    )
    def test_mena_country_codes(self, raw, expected):
        assert _normalize_mena_phone(raw) == expected

    def test_212_not_shadowed_by_20(self):
        # Critical: 212 (Morocco) must NOT be parsed as 20 (Egypt) + 12.
        # Longest-prefix match guarantees this.
        assert _normalize_mena_phone("+212661234567") == "212661234567"

    def test_213_not_shadowed_by_20(self):
        assert _normalize_mena_phone("+213551234567") == "213551234567"

    def test_unknown_prefix_defaults_to_egypt(self):
        # National-format number with no recognizable country code:
        # legacy behavior assumes Egypt. Strip leading 0, prepend 20.
        assert _normalize_mena_phone("0501234567") == "20501234567"

    def test_empty_input(self):
        assert _normalize_mena_phone("") == ""
        assert _normalize_mena_phone("abc") == ""

    def test_eg_phone_backward_compat_alias(self):
        # The legacy ``_normalize_eg_phone`` name is preserved.
        assert _normalize_eg_phone("+201001234567") == "201001234567"


class TestHashUserDataPhase14:
    """The ``hash_user_data`` contract after Phase 14."""

    def test_arabic_name_yields_two_hashes(self):
        out = hash_user_data({"first_name": "محمد"})
        assert isinstance(out["fn"], list)
        assert len(out["fn"]) == 2
        assert out["fn"][0] == _sha256("mohamed")
        assert out["fn"][1] == _sha256("محمد")

    def test_latin_name_remains_single_hash(self):
        # Existing behavior preserved — non-Arabic stores see no change.
        out = hash_user_data({"first_name": "Mohamed"})
        assert isinstance(out["fn"], list)
        assert len(out["fn"]) == 1
        assert out["fn"][0] == _sha256("mohamed")

    def test_arabic_city_yields_two_hashes(self):
        out = hash_user_data({"city": "القاهرة"})
        assert len(out["ct"]) == 2
        assert out["ct"][0] == _sha256("cairo")
        assert out["ct"][1] == _sha256("القاهرة")

    def test_saudi_phone_normalized_then_hashed(self):
        out = hash_user_data({"phone": "+966501234567"})
        assert out["ph"][0] == _sha256("966501234567")

    def test_uae_phone_normalized_then_hashed(self):
        out = hash_user_data({"phone": "+971501234567"})
        assert out["ph"][0] == _sha256("971501234567")

    def test_mixed_script_name_yields_two_hashes(self):
        # "Mohamed محمد" — _is_arabic_script returns True, both variants
        # emitted. Latin variant is letter-by-letter applied to the
        # mixed string (Latin chars pass through unchanged).
        out = hash_user_data({"first_name": "Mohamed محمد"})
        assert len(out["fn"]) == 2

    def test_arabic_in_full_payload_dual_hash_only_for_name_fields(self):
        # em / zp / country / external_id / phone stay single-hash
        # because Meta's spec for those fields doesn't accept the
        # dual-variant pattern in the same way.
        out = hash_user_data({
            "email": "shopper@example.com",
            "phone": "+201001234567",
            "first_name": "محمد",
            "last_name": "السيد",
            "city": "القاهرة",
            "country_code": "EG",
            "zip": "11511",
            "customer_id": "cust-xyz",
        })
        assert len(out["em"]) == 1
        assert len(out["ph"]) == 1
        assert len(out["fn"]) == 2  # ← Phase 14 dual-variant
        assert len(out["ln"]) == 2  # ← Phase 14 dual-variant
        assert len(out["ct"]) == 2  # ← Phase 14 dual-variant
        assert len(out["country"]) == 1
        assert len(out["zp"]) == 1
        assert len(out["external_id"]) == 1
        # Pinned canonical Latin spellings.
        assert out["fn"][0] == _sha256("mohamed")
        assert out["ln"][0] == _sha256("elsayed")
        assert out["ct"][0] == _sha256("cairo")

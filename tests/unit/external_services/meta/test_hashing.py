"""Unit tests for the Meta CAPI PII hashing helpers.

These cover plan §5.6's contract:

  * SHA-256 lowercase-trimmed for em/ph/fn/ln/ct/zp/country/external_id
  * Egyptian phone normalization to ``20 + 10 digits``
  * None / empty handling so callers can omit fields cleanly

Match-quality regressions on these helpers silently destroy ROAS
attribution, so the assertions here pin exact hex digests rather than
just "is a string of length 64".
"""

import hashlib

import pytest

from src.infrastructure.external_services.meta.hashing import (
    _h,
    _normalize_eg_phone,
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

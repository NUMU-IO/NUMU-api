"""Unit tests for TikTok Events API PII hashing.

``hash_tiktok_user_data`` reuses Meta's canonical SHA-256 + MENA-phone +
Arabic-transliteration primitives but emits TikTok's ``user`` shape. These
pin: email/phone/external_id are hashed; ttclid/ttp/ip/ua are raw; empties
are dropped; Arabic names transliterate to a stable Latin hash.
"""

from __future__ import annotations

import hashlib

from src.infrastructure.external_services.tiktok.hashing import hash_tiktok_user_data


def _sha(s: str) -> str:
    return hashlib.sha256(s.strip().lower().encode()).hexdigest()


class TestHashedIdentifiers:
    def test_email_is_sha256_lowercased_trimmed(self):
        out = hash_tiktok_user_data({"email": "  Sara@Example.COM "})
        assert out["email"] == _sha("sara@example.com")
        assert len(out["email"]) == 64

    def test_external_id_hashed_from_customer_id(self):
        out = hash_tiktok_user_data({"customer_id": "cust-123"})
        assert out["external_id"] == _sha("cust-123")

    def test_phone_normalized_to_mena_e164_then_hashed(self):
        # Egyptian national format → 20-prefixed E.164-without-plus, then SHA-256.
        out = hash_tiktok_user_data({"phone": "01001234567"})
        assert out["phone"] == _sha("201001234567")

    def test_arabic_first_name_produces_a_hash(self):
        out = hash_tiktok_user_data({"first_name": "محمد"})
        # Transliterated Latin variant is hashed (single string, not a list).
        assert isinstance(out["first_name"], str)
        assert len(out["first_name"]) == 64


class TestRawSignals:
    def test_ttclid_and_ttp_are_raw(self):
        out = hash_tiktok_user_data({"ttclid": "TT123", "ttp": "ttpcookie"})
        assert out["ttclid"] == "TT123"
        assert out["ttp"] == "ttpcookie"

    def test_ip_and_user_agent_are_raw(self):
        out = hash_tiktok_user_data({"ip": "192.0.2.1", "user_agent": "UA/1.0"})
        assert out["ip"] == "192.0.2.1"
        assert out["user_agent"] == "UA/1.0"


class TestEmptyHandling:
    def test_empty_input_yields_empty_dict(self):
        assert hash_tiktok_user_data({}) == {}

    def test_none_and_blank_fields_are_dropped(self):
        out = hash_tiktok_user_data({
            "email": "",
            "phone": None,
            "ttclid": "",
            "customer_id": "c1",
        })
        # Only the non-empty external_id survives.
        assert "email" not in out
        assert "phone" not in out
        assert "ttclid" not in out
        assert out["external_id"] == _sha("c1")


# ---------------------------------------------------------------------------
# Cross-vendor coupling with meta/hashing.py
#
# `hash_tiktok_user_data` imports `_normalize_name` from Meta's module. The
# Meta signal-quality change (2026-08-17) altered that function's output, so
# TikTok's name digests changed too — a cross-vendor behaviour change that was
# not part of the stated scope and had no test either side of it.
#
# These tests exist so the coupling is VISIBLE: any future edit to Meta's
# normalizer that moves TikTok's wire format will now fail here first.
# ---------------------------------------------------------------------------


class TestSharedNormalizerCoupling:
    def test_name_punctuation_is_stripped(self):
        """Changed 2026-08-17 via Meta's `_normalize_name`.

        Was `al-sayed`; is now `alsayed`. Both TikTok and Meta specify
        "lowercase, no punctuation" for name fields, so the new form is the
        correct one — but every TikTok event for a punctuated name now carries
        a different digest than it did before.
        """
        out = hash_tiktok_user_data({"first_name": "Al-Sayed", "last_name": "O'Brien"})
        assert out["first_name"] == _sha("alsayed")
        assert out["last_name"] == _sha("obrien")

    def test_name_internal_spaces_are_preserved(self):
        out = hash_tiktok_user_data({"last_name": "El  Masry"})
        assert out["last_name"] == _sha("el masry")


class TestVendorParityGaps:
    """TikTok and Meta must normalize the shared fields IDENTICALLY.

    These began as xfail(strict) pins: the Meta normalization fix (strip spaces
    from ct/st, strip dashes from zp, canonicalize country) was applied only to
    Meta's call sites, so for a few hours TikTok was the vendor left holding the
    unmatchable form — and `tiktok/hashing.py` imports Meta's helpers, which is
    exactly why a one-sided fix could drift silently.

    The gaps are now closed and these assert parity directly. Keep them: they
    are the tripwire for the next time one vendor's normalization is corrected
    without the other's.
    """

    def test_city_should_match_metas_normalization(self):
        from src.infrastructure.external_services.meta.hashing import hash_user_data

        raw = {"city": "New Cairo"}
        assert hash_tiktok_user_data(raw)["city"] == hash_user_data(raw)["ct"][0]

    def test_zip_should_match_metas_normalization(self):
        from src.infrastructure.external_services.meta.hashing import hash_user_data

        raw = {"zip": "12345-678"}
        assert hash_tiktok_user_data(raw)["zip_code"] == hash_user_data(raw)["zp"][0]

    def test_country_should_be_canonicalized_like_meta(self):
        from src.infrastructure.external_services.meta.hashing import hash_user_data

        raw = {"country_code": "Egypt"}
        assert (
            hash_tiktok_user_data(raw)["country"] == hash_user_data(raw)["country"][0]
        )

    def test_tiktok_sends_no_state_at_all(self):
        """Meta gained `st` in this change; TikTok's user object has no state
        key and did not get one. Recorded so the asymmetry is deliberate."""
        out = hash_tiktok_user_data({"state": "Cairo"})
        assert "state" not in out

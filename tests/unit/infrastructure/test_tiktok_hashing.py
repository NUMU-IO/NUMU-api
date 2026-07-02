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

"""Tests for the WhatsApp OTP service (backend-025 / spec 015).

Pure-function tests for the formula + verdict matrix. The route + DB
end-to-end tests are integration tests gated by the test_session fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.application.services.otp_service import (
    OTP_CODE_LENGTH,
    OTP_MAX_ATTEMPTS,
    OTP_MAX_ISSUES_PER_HOUR,
    OTP_TTL_SECONDS,
    OtpVerdict,
    codes_match,
    evaluate_verify,
    expires_at_for_now,
    generate_code,
    hash_code,
)

# ---------------------------------------------------------------------------
# generate_code — CSPRNG-based 6-digit numeric
# ---------------------------------------------------------------------------


class TestGenerateCode:
    def test_returns_six_digit_string(self):
        for _ in range(50):
            code = generate_code()
            assert len(code) == OTP_CODE_LENGTH
            assert code.isdigit()
            assert 100_000 <= int(code) <= 999_999

    def test_distribution_appears_random(self):
        # Smoke test for non-determinism — same seed never produces same
        # output twice across 100 generations (CSPRNG, not Mersenne).
        codes = {generate_code() for _ in range(100)}
        # Cardinality should be ≥ 95 in 100 draws from 900k possibilities.
        assert len(codes) >= 95


# ---------------------------------------------------------------------------
# hash_code + codes_match — HMAC + constant-time compare
# ---------------------------------------------------------------------------


class TestHashing:
    SALT = "test-platform-salt-123456789"

    def test_hash_is_deterministic_with_same_salt(self):
        h1 = hash_code("123456", self.SALT)
        h2 = hash_code("123456", self.SALT)
        assert h1 == h2

    def test_hash_changes_with_different_salt(self):
        h1 = hash_code("123456", self.SALT)
        h2 = hash_code("123456", "different-salt-012345678")
        assert h1 != h2

    def test_hash_changes_with_different_code(self):
        h1 = hash_code("123456", self.SALT)
        h2 = hash_code("123457", self.SALT)
        assert h1 != h2

    def test_hash_is_64_char_hex(self):
        h = hash_code("123456", self.SALT)
        assert len(h) == 64
        int(h, 16)  # parses as hex

    def test_codes_match_returns_true_for_correct_code(self):
        stored = hash_code("123456", self.SALT)
        assert codes_match("123456", stored, self.SALT) is True

    def test_codes_match_returns_false_for_wrong_code(self):
        stored = hash_code("123456", self.SALT)
        assert codes_match("999999", stored, self.SALT) is False

    def test_empty_salt_raises(self):
        with pytest.raises(ValueError, match="salt"):
            hash_code("123456", "")


# ---------------------------------------------------------------------------
# evaluate_verify — verdict matrix per spec 015 FR-003
# ---------------------------------------------------------------------------


class TestEvaluateVerify:
    SALT = "test-platform-salt-123456789"

    def test_correct_code_returns_verified(self):
        stored = hash_code("123456", self.SALT)
        result = evaluate_verify(
            submitted_code="123456",
            stored_hash=stored,
            salt=self.SALT,
            expires_at=datetime.now(UTC) + timedelta(minutes=4),
            attempts_left=3,
            verified_at=None,
        )
        assert result.verdict == OtpVerdict.VERIFIED

    def test_wrong_code_decrements_attempts(self):
        stored = hash_code("123456", self.SALT)
        result = evaluate_verify(
            submitted_code="999999",
            stored_hash=stored,
            salt=self.SALT,
            expires_at=datetime.now(UTC) + timedelta(minutes=4),
            attempts_left=3,
            verified_at=None,
        )
        assert result.verdict == OtpVerdict.WRONG_CODE
        assert result.attempts_left == 2

    def test_zero_attempts_left_returns_locked(self):
        stored = hash_code("123456", self.SALT)
        result = evaluate_verify(
            submitted_code="123456",
            stored_hash=stored,
            salt=self.SALT,
            expires_at=datetime.now(UTC) + timedelta(minutes=4),
            attempts_left=0,
            verified_at=None,
        )
        assert result.verdict == OtpVerdict.LOCKED

    def test_expired_returns_expired(self):
        stored = hash_code("123456", self.SALT)
        result = evaluate_verify(
            submitted_code="123456",
            stored_hash=stored,
            salt=self.SALT,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            attempts_left=3,
            verified_at=None,
        )
        assert result.verdict == OtpVerdict.EXPIRED

    def test_already_verified_returns_verified_idempotently(self):
        """Re-verify of an already-verified row → same verdict, no decrement."""
        stored = hash_code("123456", self.SALT)
        already_verified = datetime.now(UTC) - timedelta(seconds=10)
        # Even with WRONG submitted code, an already-verified row stays verified.
        result = evaluate_verify(
            submitted_code="999999",  # wrong code
            stored_hash=stored,
            salt=self.SALT,
            expires_at=datetime.now(UTC) + timedelta(minutes=4),
            attempts_left=3,
            verified_at=already_verified,
        )
        assert result.verdict == OtpVerdict.VERIFIED
        assert result.attempts_left == 3  # No decrement


# ---------------------------------------------------------------------------
# Constants are pinned (spec 015 FR-005)
# ---------------------------------------------------------------------------


class TestConstants:
    def test_ttl_is_5_minutes(self):
        assert OTP_TTL_SECONDS == 5 * 60

    def test_max_attempts_is_3(self):
        assert OTP_MAX_ATTEMPTS == 3

    def test_max_issues_per_hour_is_5(self):
        assert OTP_MAX_ISSUES_PER_HOUR == 5

    def test_code_length_is_6(self):
        assert OTP_CODE_LENGTH == 6

    def test_expires_at_for_now_is_5_min_ahead(self):
        now = datetime.now(UTC)
        expires = expires_at_for_now(now=now)
        delta = (expires - now).total_seconds()
        assert delta == OTP_TTL_SECONDS

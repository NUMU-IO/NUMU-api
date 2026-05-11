"""WhatsApp OTP issuance + verification (backend-025 / spec 015).

Pure-function helpers + an async service class. The service handles
the DB persistence and emits :class:`OtpVerifiedEvent` on success.

Constitution Principle II: cleartext codes never persist; only the
HMAC hash. Verify uses ``hmac.compare_digest`` for constant-time match.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

# ---------------------------------------------------------------------------
# Constants (spec 015 FR-005)
# ---------------------------------------------------------------------------

OTP_TTL_SECONDS = 5 * 60  # 5 minutes
OTP_MAX_ATTEMPTS = 3
OTP_MAX_ISSUES_PER_HOUR = 5
OTP_CODE_LENGTH = 6


# ---------------------------------------------------------------------------
# Pure helpers (snapshot-testable)
# ---------------------------------------------------------------------------


def generate_code() -> str:
    """Generate a cryptographically-random 6-digit numeric code as string.

    Uses ``secrets`` (CSPRNG) — the LSB of a hash is NOT a code source.
    """
    n = secrets.randbelow(900_000) + 100_000  # 100000..999999 inclusive
    return str(n)


def hash_code(code: str, salt: str) -> str:
    """HMAC-SHA256 of the cleartext code with the platform pepper."""
    if not salt:
        raise ValueError("OTP hash salt is required (PLATFORM_SECRET_SALT)")
    return hmac.new(
        salt.encode("utf-8"),
        code.encode("utf-8"),
        sha256,
    ).hexdigest()


def codes_match(submitted: str, stored_hash: str, salt: str) -> bool:
    """Constant-time compare — never short-circuits on mismatched chars."""
    return hmac.compare_digest(hash_code(submitted, salt), stored_hash)


# ---------------------------------------------------------------------------
# Verdict enum + result record
# ---------------------------------------------------------------------------


class OtpVerdict(StrEnum):
    VERIFIED = "verified"
    WRONG_CODE = "wrong_code"
    LOCKED = "locked"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass
class OtpVerifyResult:
    verdict: OtpVerdict
    attempts_left: int = 0
    otp_id: UUID | None = None


# ---------------------------------------------------------------------------
# Issuance / verification logic (no DB — used by the route layer)
# ---------------------------------------------------------------------------


def evaluate_verify(
    *,
    submitted_code: str,
    stored_hash: str,
    salt: str,
    expires_at: datetime,
    attempts_left: int,
    verified_at: datetime | None,
    now: datetime | None = None,
) -> OtpVerifyResult:
    """Pure-function verify decision.

    Encapsulates the spec 015 FR-003 verdict matrix so the route layer
    just persists the resulting state.
    """
    now_dt = now or datetime.now(UTC)

    # Re-verify of an already-verified row → idempotent success.
    if verified_at is not None:
        return OtpVerifyResult(verdict=OtpVerdict.VERIFIED, attempts_left=attempts_left)

    if expires_at <= now_dt:
        return OtpVerifyResult(verdict=OtpVerdict.EXPIRED, attempts_left=0)

    if attempts_left <= 0:
        return OtpVerifyResult(verdict=OtpVerdict.LOCKED, attempts_left=0)

    if codes_match(submitted_code, stored_hash, salt):
        return OtpVerifyResult(verdict=OtpVerdict.VERIFIED, attempts_left=attempts_left)

    return OtpVerifyResult(
        verdict=OtpVerdict.WRONG_CODE,
        attempts_left=attempts_left - 1,
    )


def expires_at_for_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(seconds=OTP_TTL_SECONDS)

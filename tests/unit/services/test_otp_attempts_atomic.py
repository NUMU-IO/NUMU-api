"""OTP attempts are spent atomically, and a verified code still expires.

Verify used to read ``attempts_left``, compare, then write the decrement, so
parallel guesses all saw the same count and a burst got far more than three
tries at a 4-digit code. Re-verifying an already-verified row also skipped
the expiry check, so otp_id + phone logged in with any code forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services.otp_service import (
    OTP_MAX_ATTEMPTS,
    OtpVerdict,
    evaluate_verify,
    hash_code,
    verify_otp_row,
)
from src.infrastructure.database.models.tenant.otp_code import OtpCodeModel

SALT = "test-platform-salt"


@pytest.mark.asyncio
async def test_parallel_guesses_share_three_attempts(test_session):
    otp = OtpCodeModel(
        tenant_id=uuid4(),
        store_id=uuid4(),
        phone_hash="p" * 64,
        code_hash=hash_code("1234", SALT),
        language="ar",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        attempts_left=OTP_MAX_ATTEMPTS,
    )
    test_session.add(otp)
    await test_session.commit()

    # Every request read the row before any of them wrote: one snapshot of
    # it stands in for that burst.
    stale = SimpleNamespace(
        id=otp.id,
        code_hash=otp.code_hash,
        expires_at=otp.expires_at,
        attempts_left=otp.attempts_left,
        verified_at=None,
    )
    verdicts = [
        (await verify_otp_row(test_session, stale, "9999", SALT)).verdict
        for _ in range(5)
    ]
    right = await verify_otp_row(test_session, stale, "1234", SALT)

    assert verdicts == [OtpVerdict.WRONG_CODE] * 3 + [OtpVerdict.LOCKED] * 2
    assert right.verdict == OtpVerdict.LOCKED, "the right code came too late"


def test_verified_code_still_expires():
    result = evaluate_verify(
        submitted_code="0000",
        stored_hash=hash_code("1234", SALT),
        salt=SALT,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        attempts_left=3,
        verified_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    assert result.verdict == OtpVerdict.EXPIRED

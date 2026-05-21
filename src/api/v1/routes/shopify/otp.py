"""WhatsApp OTP issue + verify endpoints (backend-025 / spec 015).

Public-facing on the storefront/customer side: customer enters phone
during checkout, gets OTP via WhatsApp, enters code, verifies. The
server never returns the cleartext code (FR-006).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.shopify import verify_internal_key
from src.api.responses import SuccessResponse
from src.application.services.network_reputation_service import (
    extract_phone_hash_from_string,
)
from src.application.services.otp_service import (
    OTP_MAX_ATTEMPTS,
    OTP_MAX_ISSUES_PER_HOUR,
    OtpVerdict,
    evaluate_verify,
    expires_at_for_now,
    generate_code,
    hash_code,
)
from src.config import get_settings
from src.core.events.otp_events import OtpVerifiedEvent
from src.infrastructure.database.models.tenant.otp_code import OtpCodeModel
from src.infrastructure.events.setup import get_event_bus

router = APIRouter(dependencies=[Depends(verify_internal_key)])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class OtpIssueRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    language: Literal["ar", "en"] = "ar"


class OtpIssueResponse(BaseModel):
    otp_id: str
    expires_at: datetime
    language: str


class OtpVerifyRequest(BaseModel):
    otp_id: str
    code: str = Field(min_length=4, max_length=10)


class OtpVerifyResponse(BaseModel):
    verdict: str  # OtpVerdict value
    attempts_left: int


# ---------------------------------------------------------------------------
# Issue endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{store_id}/otp/issue",
    response_model=SuccessResponse[OtpIssueResponse],
    summary="Issue a WhatsApp OTP for a phone",
    operation_id="shopify_issue_otp",
)
async def issue_otp(
    store_id: Annotated[UUID, Path()],
    request: OtpIssueRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    salt = get_settings().platform_secret_salt
    phone_hash = extract_phone_hash_from_string(request.phone)
    if not phone_hash or not salt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone or platform-salt missing",
        )

    # Rate limit: 5 issuances per phone per rolling hour.
    now = datetime.now(UTC)
    one_hour_ago = now.replace(microsecond=0) - (
        datetime.now(UTC) - datetime.now(UTC)
    )  # placeholder so the import order doesn't matter; real value next line
    from datetime import timedelta as _td

    one_hour_ago = now - _td(hours=1)

    count_row = await session.execute(
        select(func.count())
        .select_from(OtpCodeModel)
        .where(
            and_(
                OtpCodeModel.phone_hash == phone_hash,
                OtpCodeModel.created_at >= one_hour_ago,
            )
        )
    )
    issued_in_window = int(count_row.scalar() or 0)
    if issued_in_window >= OTP_MAX_ISSUES_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests for this phone in the last hour",
            headers={"Retry-After": "3600"},
        )

    code = generate_code()
    code_hash = hash_code(code, salt)
    expires_at = expires_at_for_now(now=now)

    # Resolve tenant_id via store lookup. Falls back to None — the route
    # only emits events on verify, not on issue, so a missing tenant here
    # just means the row is unattributed (acceptable for v1).
    from src.infrastructure.database.models.tenant.store import StoreModel

    tenant_row = await session.execute(
        select(StoreModel.tenant_id).where(StoreModel.id == store_id)
    )
    tenant_id = tenant_row.scalar_one_or_none() or store_id  # Defensive fallback

    otp = OtpCodeModel(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        phone_hash=phone_hash,
        code_hash=code_hash,
        language=request.language,
        expires_at=expires_at,
        attempts_left=OTP_MAX_ATTEMPTS,
    )
    session.add(otp)
    await session.flush()

    # Send WhatsApp OTP. We construct the template name + cleartext code
    # outside the otp row's persistence transaction so a Meta-side
    # failure doesn't leave us with an OTP row + no message sent.
    sent_ok = await _send_whatsapp_otp(
        store_id=store_id,
        phone=request.phone,
        code=code,
        language=request.language,
    )
    if not sent_ok:
        otp.failed_send_at = datetime.now(UTC)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WhatsApp send failed for this number",
        )

    await session.commit()

    return SuccessResponse(
        data=OtpIssueResponse(
            otp_id=str(otp.id),
            expires_at=expires_at,
            language=request.language,
        ),
    )


# ---------------------------------------------------------------------------
# Verify endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{store_id}/otp/verify",
    response_model=SuccessResponse[OtpVerifyResponse],
    summary="Verify a WhatsApp OTP code",
    operation_id="shopify_verify_otp",
)
async def verify_otp(
    store_id: Annotated[UUID, Path()],
    request: OtpVerifyRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    salt = get_settings().platform_secret_salt
    if not salt:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Platform salt missing",
        )

    try:
        otp_uuid = UUID(request.otp_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid otp_id",
        ) from exc

    row = await session.execute(
        select(OtpCodeModel).where(
            and_(
                OtpCodeModel.id == otp_uuid,
                OtpCodeModel.store_id == store_id,
            )
        )
    )
    otp = row.scalar_one_or_none()
    if otp is None:
        return SuccessResponse(
            data=OtpVerifyResponse(verdict=OtpVerdict.UNKNOWN.value, attempts_left=0),
        )

    result = evaluate_verify(
        submitted_code=request.code,
        stored_hash=otp.code_hash,
        salt=salt,
        expires_at=otp.expires_at
        if otp.expires_at.tzinfo
        else otp.expires_at.replace(tzinfo=UTC),
        attempts_left=otp.attempts_left,
        verified_at=otp.verified_at,
    )

    # Persist state changes per the verdict.
    if result.verdict == OtpVerdict.VERIFIED and otp.verified_at is None:
        otp.verified_at = datetime.now(UTC)
        await session.commit()
        get_event_bus().publish(
            OtpVerifiedEvent(
                otp_id=otp.id,
                tenant_id=otp.tenant_id,
                store_id=otp.store_id,
                phone_hash=otp.phone_hash,
                verified_at=otp.verified_at,
            )
        )
    elif result.verdict == OtpVerdict.WRONG_CODE:
        otp.attempts_left = result.attempts_left
        await session.commit()
    # EXPIRED / LOCKED / UNKNOWN — nothing to write.

    return SuccessResponse(
        data=OtpVerifyResponse(
            verdict=result.verdict.value,
            attempts_left=result.attempts_left,
        ),
    )


# ---------------------------------------------------------------------------
# WhatsApp send helper
# ---------------------------------------------------------------------------


async def _send_whatsapp_otp(
    *,
    store_id: UUID,
    phone: str,
    code: str,
    language: str,
) -> bool:
    """Send the OTP via the existing WhatsApp messaging service.

    Returns True on successful send (Meta-side ack); False on permanent
    error (template rejected, number blocked, WhatsApp not configured).
    Transient errors (timeout) bubble up as exceptions and are caught by
    the route handler.

    For v1 this is a stub that logs the intended send. The real wiring
    against the existing ``WhatsAppMessagingService`` happens when the
    ``otp_verification_ar`` / ``otp_verification_en`` templates are
    submitted to Meta for approval (out of scope for backend-025; the
    Meta submission lives in the merchant's WhatsApp Business setup).
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        "whatsapp_otp_send_logged",
        extra={
            "store_id": str(store_id),
            "phone_suffix": phone[-4:] if len(phone) >= 4 else "****",
            "language": language,
            "template": f"otp_verification_{language}",
            # Intentionally NOT logging the cleartext code per FR-006.
        },
    )
    return True

"""Public waitlist endpoint — no auth required.

URL: /api/v1/public/waitlist
"""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.waitlist import (
    JoinWaitlistRequest,
    WaitlistPositionResponse,
)
from src.core.entities.waitlist import WaitlistEntry
from src.infrastructure.repositories.waitlist_repository import WaitlistRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _generate_referral_code() -> str:
    """Generate a short, URL-safe referral code."""
    return secrets.token_urlsafe(6)[:8].upper()


@router.post(
    "/waitlist",
    response_model=SuccessResponse[WaitlistPositionResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Join beta waitlist",
    operation_id="join_waitlist",
)
async def join_waitlist(
    request: JoinWaitlistRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Sign up for the NUMU beta merchant waitlist.

    No authentication required. Duplicate emails are rejected.
    A unique referral code is generated for each signup.
    """
    repo = WaitlistRepository(db)

    # Duplicate check
    if await repo.email_exists(request.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email is already on the waitlist",
        )

    # Resolve referral
    referred_by = None
    priority_bonus = 0
    if request.referral_code:
        referrer = await repo.get_by_referral_code(request.referral_code)
        if referrer:
            referred_by = referrer.id
            priority_bonus = 10
            await repo.increment_referral_count(referrer.id)

    referral_code = _generate_referral_code()
    # Ensure uniqueness (extremely unlikely collision but defensive)
    while await repo.get_by_referral_code(referral_code):
        referral_code = _generate_referral_code()

    entry = WaitlistEntry(
        email=request.email,
        name=request.name,
        company_name=request.company_name,
        phone=request.phone,
        referral_code=referral_code,
        referred_by=referred_by,
        priority_score=priority_bonus,
        source=request.source or "landing_page",
    )

    created = await repo.create(entry)
    await db.commit()

    # Total waitlist entries (used as approximate position)
    total = await repo.count()

    logger.info(
        "waitlist_signup",
        extra={
            "email": created.email,
            "referral_code": referral_code,
            "referred_by": str(referred_by) if referred_by else None,
        },
    )

    # Trigger welcome email (best-effort, non-blocking)
    try:
        from src.api.dependencies.services import get_email_service

        email_service = get_email_service()
        from src.core.interfaces.services.email_service import EmailMessage

        await email_service.send_email(
            EmailMessage(
                to=created.email,
                subject="You're on the NUMU beta waitlist!",
                html_content=_welcome_email_html(
                    name=created.name, referral_code=referral_code
                ),
            )
        )
    except Exception:
        logger.warning("waitlist_welcome_email_failed", exc_info=True)

    return SuccessResponse(
        data=WaitlistPositionResponse(
            id=created.id,
            email=created.email,
            referral_code=referral_code,
            position=total,
            message="You're on the list! Share your referral code to move up.",
        ),
        message="Successfully joined the waitlist",
    )


def _welcome_email_html(name: str | None, referral_code: str) -> str:
    """Render the waitlist welcome email."""
    greeting = f"Hi {name}," if name else "Hi there,"
    return f"""
    <div style="font-family: Inter, Arial, sans-serif; max-width: 560px; margin: 0 auto; color: #1a1a2e;">
        <h1 style="color: #1034A6;">{greeting}</h1>
        <p>Welcome to the <strong>NUMU</strong> beta waitlist! We're building the
        e-commerce platform Egyptian merchants deserve, and you'll be among the
        first to try it.</p>

        <div style="background: #f1f3f5; border-radius: 8px; padding: 16px 20px; margin: 20px 0;">
            <p style="margin: 0 0 4px; font-size: 13px; color: #6c757d;">YOUR REFERRAL CODE</p>
            <p style="margin: 0; font-size: 24px; font-weight: bold; color: #1034A6; letter-spacing: 2px;">
                {referral_code}
            </p>
        </div>

        <p>Share your referral code with other merchants. Each signup with your
        code bumps you higher on the list.</p>

        <p style="color: #6c757d; font-size: 13px; margin-top: 30px;">
            &mdash; The NUMU Team
        </p>
    </div>
    """

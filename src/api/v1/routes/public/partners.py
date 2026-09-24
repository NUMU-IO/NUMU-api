"""Public partner directory ("Hire an expert") — no auth required.

URL: /api/v1/public/partners. Feeds ``https://numueg.app/partners``.

Only approved partners who opted in (``directory_listed``) and whom an admin
has not hidden are shown, and only while the Partner program is open. A
profile carries what the partner chose to publish plus their published apps
and themes; never an email, phone or anything about their merchants. The
contact form mails the partner's support address with the sender as
reply-to, so the address itself stays private.
"""

from __future__ import annotations

from html import escape
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.services import get_email_service
from src.api.middleware.rate_limit import _get_client_ip, rate_limit_exceeded_response
from src.api.responses import SuccessResponse
from src.application.services.lockout_service import EmailActionThrottle
from src.application.services.partner_program import program_enabled
from src.core.entities.app import AppStatus
from src.core.entities.marketplace_theme import MarketplaceThemeStatus
from src.core.interfaces.services.email_service import EmailMessage, IEmailService
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
    PartnerReferralModel,
)
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeModel,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/partners")

#: "Top partner" is earned, not granted: this many active installs of the
#: partner's apps, or this many referred merchants who paid for a plan.
TOP_MIN_INSTALLS = 50
TOP_MIN_PAID_REFERRALS = 10

Service = Literal["apps", "themes", "setup", "marketing"]


class DirectoryPartner(BaseModel):
    id: UUID
    display_name: str
    website_url: str | None
    logo_url: str | None
    bio_ar: str | None
    bio_en: str | None
    services: list[str]
    languages: list[str]
    city: str | None
    badges: list[Literal["verified", "top"]]


class ListingApp(BaseModel):
    name: str
    slug: str
    icon_url: str | None


class ListingTheme(BaseModel):
    name: str
    slug: str
    thumbnail_url: str | None


class DirectoryProfile(DirectoryPartner):
    apps: list[ListingApp]
    themes: list[ListingTheme]


class ContactRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    message: str = Field(min_length=10, max_length=2000)


def _listed():
    return select(PartnerAccountModel).where(
        PartnerAccountModel.status == "approved",
        PartnerAccountModel.directory_listed.is_(True),
        PartnerAccountModel.directory_hidden.is_(False),
    )


async def _top(db: AsyncSession, partners: list[PartnerAccountModel]) -> set[UUID]:
    if not partners:
        return set()
    installs = dict(
        (
            await db.execute(
                select(AppModel.developer_id, func.count(AppInstallationModel.id))
                .join(AppInstallationModel, AppInstallationModel.app_id == AppModel.id)
                .where(
                    AppModel.developer_id.in_([p.user_id for p in partners]),
                    AppInstallationModel.is_enabled.is_(True),
                    AppInstallationModel.status == "active",
                )
                .group_by(AppModel.developer_id)
            )
        ).all()
    )
    paid = dict(
        (
            await db.execute(
                select(PartnerReferralModel.partner_id, func.count())
                .where(
                    PartnerReferralModel.partner_id.in_([p.id for p in partners]),
                    PartnerReferralModel.first_paid_at.is_not(None),
                )
                .group_by(PartnerReferralModel.partner_id)
            )
        ).all()
    )
    return {
        p.id
        for p in partners
        if installs.get(p.user_id, 0) >= TOP_MIN_INSTALLS
        or paid.get(p.id, 0) >= TOP_MIN_PAID_REFERRALS
    }


def _card(p: PartnerAccountModel, top: set[UUID]) -> DirectoryPartner:
    profile = p.directory_profile or {}
    return DirectoryPartner(
        id=p.id,
        display_name=p.display_name,
        website_url=p.website_url,
        logo_url=profile.get("logo_url"),
        bio_ar=profile.get("bio_ar"),
        bio_en=profile.get("bio_en"),
        services=profile.get("services") or [],
        languages=profile.get("languages") or [],
        city=profile.get("city"),
        badges=[b for b, on in (("verified", p.verified), ("top", p.id in top)) if on],
    )


async def _load(db: AsyncSession, partner_id: UUID) -> PartnerAccountModel:
    p = (
        await db.execute(_listed().where(PartnerAccountModel.id == partner_id))
    ).scalar_one_or_none()
    if p is None or not await program_enabled(db):
        raise HTTPException(status_code=404, detail="Partner not found")
    return p


@router.get(
    "",
    response_model=SuccessResponse[list[DirectoryPartner]],
    operation_id="list_directory_partners",
)
async def list_partners(
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Service | None = None,
    badge: Literal["verified", "top"] | None = None,
):
    """Verified partners first, then alphabetical."""
    if not await program_enabled(db):
        return SuccessResponse(data=[])
    # ponytail: filters run in Python over every listed partner; move them
    # into SQL once the directory holds thousands.
    partners = (
        (
            await db.execute(
                _listed().order_by(
                    PartnerAccountModel.verified.desc(),
                    PartnerAccountModel.display_name,
                )
            )
        )
        .scalars()
        .all()
    )
    top = await _top(db, list(partners))
    cards = [_card(p, top) for p in partners]
    return SuccessResponse(
        data=[
            c
            for c in cards
            if (service is None or service in c.services)
            and (badge is None or badge in c.badges)
        ]
    )


@router.get(
    "/{partner_id}",
    response_model=SuccessResponse[DirectoryProfile],
    operation_id="get_directory_partner",
)
async def get_partner(partner_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]):
    p = await _load(db, partner_id)
    apps = (
        await db.execute(
            select(AppModel.name, AppModel.slug, AppModel.icon_url)
            .where(
                AppModel.developer_id == p.user_id,
                AppModel.status == AppStatus.PUBLISHED,
            )
            .order_by(AppModel.name)
        )
    ).all()
    themes = (
        await db.execute(
            select(
                MarketplaceThemeModel.name,
                MarketplaceThemeModel.slug,
                MarketplaceThemeModel.thumbnail_url,
            )
            .where(
                MarketplaceThemeModel.developer_id == p.user_id,
                MarketplaceThemeModel.status == MarketplaceThemeStatus.PUBLISHED.value,
            )
            .order_by(MarketplaceThemeModel.name)
        )
    ).all()
    card = _card(p, await _top(db, [p]))
    return SuccessResponse(
        data=DirectoryProfile(
            **card.model_dump(),
            apps=[
                ListingApp(name=a.name, slug=a.slug, icon_url=a.icon_url) for a in apps
            ],
            themes=[
                ListingTheme(name=t.name, slug=t.slug, thumbnail_url=t.thumbnail_url)
                for t in themes
            ],
        )
    )


@router.post(
    "/{partner_id}/contact",
    response_model=SuccessResponse[dict],
    operation_id="contact_directory_partner",
)
async def contact_partner(
    partner_id: UUID,
    body: ContactRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[IEmailService, Depends(get_email_service)],
):
    """Email the partner. Rate limited per visitor, per sender and per partner."""
    p = await _load(db, partner_id)
    throttle = EmailActionThrottle(RedisCacheService())
    for action, key, limit, window in (
        ("partner_contact_ip", _get_client_ip(request), 5, 3600),
        ("partner_contact_from", str(body.email), 3, 3600),
        ("partner_contact_to", str(p.id), 20, 86400),
    ):
        allowed, retry_after = await throttle.hit(
            action, key, limit=limit, window_seconds=window
        )
        if not allowed:
            return rate_limit_exceeded_response(retry_after)

    name, sender = escape(body.name), escape(str(body.email))
    message = escape(body.message)
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px">
      <p dir="rtl">رسالة جديدة من دليل شركاء نُمو. للرد، أجب على هذا البريد.</p>
      <p>A new message from the NUMU partner directory. Reply to this email to answer.</p>
      <p><strong>{name}</strong> &lt;{sender}&gt;</p>
      <p style="white-space:pre-wrap;padding:16px;background:#f9fafb;border-radius:12px">{message}</p>
    </div>
    """
    try:
        await email_service.send_email(
            EmailMessage(
                to=p.support_email,
                subject=f"[NUMU] رسالة من دليل الشركاء / Directory inquiry: {' '.join(body.name.split())}",
                html_content=html,
                reply_to=str(body.email),
            )
        )
    except Exception:
        logger.exception("partner_contact_email_failed", partner_id=str(p.id))
        raise HTTPException(
            status_code=502, detail="Could not send your message. Try again later."
        ) from None
    return SuccessResponse(data={"sent": True}, message="Message sent")

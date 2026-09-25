"""Partner App review rounds, store listings, and the partner status fan-out.

A review round covers a version, a listing, or both (``app_reviews``). The
listing (name, tagline, description, screenshots, video, category,
keywords) lives in ``app_listings`` apart from the manifest, so a new
version can never silently rename an app: the name changes only through a
reviewed listing.

Every status change a partner cares about goes through ``notify_status``:
one feed row (``emit_partner_notification``) and one bilingual email to the
partner's owner and admins.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.app_manifest import CATEGORIES, Bilingual, Screenshot
from src.application.services.partner_notifications import emit_partner_notification
from src.application.services.partner_program import partner_for_user
from src.config import settings
from src.core.interfaces.services.email_service import EmailMessage
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppListingModel,
    AppModel,
    AppReviewModel,
    AppVersionModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerMemberModel,
)
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.external_services.resend.email_service import (
    ResendEmailService,
)
from src.infrastructure.external_services.resend.email_templates.partner_apps import (
    partner_app_status_html,
    partner_app_status_subject,
)

logger = get_logger(__name__)

#: NUMU's review target, in business days (Egypt: Friday and Saturday off).
REVIEW_SLA_BUSINESS_DAYS = 3
WEEKEND = (4, 5)

OPEN = ("submitted", "in_review")
LISTING_EDITABLE = ("draft", "changes_requested")
VIDEO_HOSTS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "vimeo.com",
    "www.vimeo.com",
    "player.vimeo.com",
})


def add_business_days(start: datetime, days: int) -> datetime:
    out = start
    while days > 0:
        out += timedelta(days=1)
        if out.weekday() not in WEEKEND:
            days -= 1
    return out


def business_days_between(start: datetime, end: datetime) -> int:
    n, day = 0, start
    while (day := day + timedelta(days=1)) <= end:
        n += day.weekday() not in WEEKEND
    return n


def due_at(submitted_at: datetime) -> datetime:
    return add_business_days(submitted_at, REVIEW_SLA_BUSINESS_DAYS)


# ─── Listing ──────────────────────────────────────────────────────


class Keywords(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ar: list[str] = Field(default_factory=list, max_length=10)
    en: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("ar", "en")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out = [k.strip() for k in v if k.strip()]
        if any(len(k) > 40 for k in out):
            raise ValueError("each keyword must be 40 characters or fewer")
        return list(dict.fromkeys(out))


def _video_url(v: str | None) -> str | None:
    if not v:
        return None
    parsed = urlparse(v.strip())
    if parsed.scheme != "https" or (parsed.hostname or "") not in VIDEO_HOSTS:
        raise ValueError("video_url must be an https YouTube or Vimeo link")
    return v.strip()


class ListingContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Bilingual
    tagline: Bilingual
    description: Bilingual
    screenshots: list[Screenshot] = Field(default_factory=list, max_length=8)
    video_url: str | None = None
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    keywords: Keywords = Field(default_factory=Keywords)

    @field_validator("name")
    @classmethod
    def _name(cls, v: Bilingual) -> Bilingual:
        return _max(v, 100, "name")

    @field_validator("tagline")
    @classmethod
    def _tagline(cls, v: Bilingual) -> Bilingual:
        return _max(v, 80, "tagline")

    @field_validator("description")
    @classmethod
    def _description(cls, v: Bilingual) -> Bilingual:
        return _max(v, 4000, "description")

    @field_validator("video_url")
    @classmethod
    def _video(cls, v: str | None) -> str | None:
        return _video_url(v)


class LooseBilingual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ar: str = Field(default="", max_length=4000)
    en: str = Field(default="", max_length=4000)


class ListingDraftContent(BaseModel):
    """The listing as the portal saves it while the partner types: any text
    may still be empty. ``ListingContent`` applies when it is submitted."""

    model_config = ConfigDict(extra="forbid")

    name: LooseBilingual = Field(default_factory=LooseBilingual)
    tagline: LooseBilingual = Field(default_factory=LooseBilingual)
    description: LooseBilingual = Field(default_factory=LooseBilingual)
    screenshots: list[Screenshot] = Field(default_factory=list, max_length=8)
    video_url: str | None = None
    category: Literal[CATEGORIES] = "other"  # type: ignore[valid-type]
    keywords: Keywords = Field(default_factory=Keywords)

    @field_validator("video_url")
    @classmethod
    def _video(cls, v: str | None) -> str | None:
        return _video_url(v)


def _max(v: Bilingual, n: int, what: str) -> Bilingual:
    if len(v.ar) > n or len(v.en) > n:
        raise ValueError(f"{what} must be {n} characters or fewer in each language")
    return v


def _names(app: AppModel) -> dict[str, str]:
    locales = (app.manifest or {}).get("app_locales") or {}
    return {
        lang: (locales.get(lang) or {}).get("name") or app.name for lang in ("ar", "en")
    }


def live_listing(app: AppModel) -> dict[str, Any]:
    """The listing merchants see now, read back from ``apps.manifest``."""
    m = app.manifest or {}
    app_locales = m.get("app_locales") or {}
    locales = m.get("locales") or {}
    return {
        "name": _names(app),
        "tagline": {
            lang: (locales.get(lang) or {}).get("tagline") or m.get("tagline") or ""
            for lang in ("ar", "en")
        },
        "description": {
            lang: (app_locales.get(lang) or {}).get("description")
            or app.description
            or ""
            for lang in ("ar", "en")
        },
        "screenshots": [
            {
                "src": s.get("url"),
                "caption": {
                    lang: ((s.get("locales") or {}).get(lang) or {}).get("caption", "")
                    for lang in ("ar", "en")
                },
            }
            for s in m.get("screenshots") or []
            if isinstance(s, dict) and s.get("url")
        ],
        "video_url": m.get("video_url"),
        "category": app.category or "other",
        "keywords": m.get("keywords") or {"ar": [], "en": []},
    }


def listing_manifest(manifest: dict[str, Any], content: dict[str, Any]) -> dict:
    """``manifest`` (the ``apps.manifest`` shape) with the listing laid over."""
    out = dict(manifest)
    out["app_locales"] = {
        lang: {
            **((manifest.get("app_locales") or {}).get(lang) or {}),
            "name": content["name"][lang],
            "description": content["description"][lang],
        }
        for lang in ("ar", "en")
    }
    out["locales"] = {
        lang: {
            **((manifest.get("locales") or {}).get(lang) or {}),
            "tagline": content["tagline"][lang],
        }
        for lang in ("ar", "en")
    }
    out["tagline"] = content["tagline"]["en"]
    out["screenshots"] = [
        {
            "url": s["src"],
            "locales": {
                lang: {"caption": (s.get("caption") or {}).get(lang, "")}
                for lang in ("ar", "en")
            },
        }
        for s in content.get("screenshots") or []
    ]
    out["video_url"] = content.get("video_url")
    out["keywords"] = content.get("keywords") or {"ar": [], "en": []}
    return out


def apply_listing(app: AppModel, content: dict[str, Any]) -> None:
    app.manifest = listing_manifest(app.manifest or {}, content)
    app.name = content["name"]["en"]
    app.description = content["tagline"]["en"]
    app.category = content["category"]


def keep_names(app: AppModel, manifest: dict[str, Any]) -> dict[str, Any]:
    """A manifest-built listing with the app's current name kept: the
    manifest ``name`` never renames an app, only a reviewed listing does."""
    names = _names(app)
    return {
        **manifest,
        "app_locales": {
            lang: {
                **((manifest.get("app_locales") or {}).get(lang) or {}),
                "name": names[lang],
            }
            for lang in ("ar", "en")
        },
    }


def name_change(app: AppModel, content: dict[str, Any] | None) -> dict | None:
    if not content:
        return None
    current = _names(app)
    proposed = {lang: content["name"][lang] for lang in ("ar", "en")}
    return None if proposed == current else {"from": current, "to": proposed}


async def editable_listing(db: AsyncSession, app_id: UUID) -> AppListingModel | None:
    return await db.scalar(
        select(AppListingModel)
        .where(
            AppListingModel.app_id == app_id,
            AppListingModel.status.in_(LISTING_EDITABLE),
        )
        .order_by(AppListingModel.created_at.desc())
        .limit(1)
    )


async def go_live(db: AsyncSession, app: AppModel, listing: AppListingModel) -> None:
    for old in (
        await db.scalars(
            select(AppListingModel).where(
                AppListingModel.app_id == app.id, AppListingModel.status == "live"
            )
        )
    ).all():
        old.status = "superseded"
    apply_listing(app, listing.content)
    listing.status = "live"


# ─── Review rounds ────────────────────────────────────────────────


def subject_of(review: AppReviewModel) -> str:
    if review.version_id and review.listing_id:
        return "version_listing"
    return "version" if review.version_id else "listing"


async def open_review_of(db: AsyncSession, app_id: UUID) -> AppReviewModel | None:
    return await db.scalar(
        select(AppReviewModel).where(
            AppReviewModel.app_id == app_id, AppReviewModel.status.in_(OPEN)
        )
    )


async def start_round(
    db: AsyncSession,
    app: AppModel,
    *,
    version: AppVersionModel | None = None,
    listing: AppListingModel | None = None,
) -> AppReviewModel:
    """A new review round. The caller has checked no round is open."""
    now = datetime.now(UTC)
    count = await db.scalar(
        select(func.count(AppReviewModel.id)).where(AppReviewModel.app_id == app.id)
    )
    for item in (version, listing):
        if item is not None:
            item.status = "submitted"
            item.submitted_at = now
    if listing is not None and version is not None:
        listing.version_id = version.id
    review = AppReviewModel(
        id=uuid4(),
        app_id=app.id,
        version_id=version.id if version else None,
        listing_id=listing.id if listing else None,
        round=(count or 0) + 1,
        status="submitted",
        submitted_at=now,
    )
    db.add(review)
    await db.flush()
    await notify_status(
        db,
        app,
        "submitted",
        version=version.version if version else None,
        listing=listing is not None,
    )
    return review


async def queue_position(db: AsyncSession, review: AppReviewModel) -> tuple[int, int]:
    ahead = await db.scalar(
        select(func.count(AppReviewModel.id)).where(
            AppReviewModel.status.in_(OPEN),
            AppReviewModel.submitted_at < review.submitted_at,
        )
    )
    total = await db.scalar(
        select(func.count(AppReviewModel.id)).where(AppReviewModel.status.in_(OPEN))
    )
    return (ahead or 0) + 1, total or 0


# ─── Partner fan-out ──────────────────────────────────────────────

_SUBJECT_LABELS = {
    "version": ("الإصدار v{v}", "Version v{v}"),
    "listing": ("صفحة التطبيق في المتجر", "Store listing"),
    "app": ("التطبيق", "The app"),
}


async def _recipients(db: AsyncSession, partner) -> list[str]:
    owner = await db.scalar(
        select(UserModel.email).where(UserModel.id == partner.user_id)
    )
    admins = (
        await db.scalars(
            select(PartnerMemberModel.email).where(
                PartnerMemberModel.partner_id == partner.id,
                PartnerMemberModel.role == "admin",
                PartnerMemberModel.status == "active",
            )
        )
    ).all()
    return list(dict.fromkeys(e for e in (owner, *admins) if e))


async def notify_status(
    db: AsyncSession,
    app: AppModel,
    status: str,
    *,
    version: str | None = None,
    listing: bool = False,
    notes: dict | None = None,
) -> None:
    """Feed row + email for one status change of a Partner App. The email
    is best-effort; the feed row is part of the caller's transaction."""
    if app.developer_id is None:
        return
    partner = await partner_for_user(db, app.developer_id)
    if partner is None:
        return
    subject = "version" if version else "listing" if listing else "app"
    await emit_partner_notification(
        db,
        partner_id=partner.id,
        kind="review_status",
        app_id=app.id,
        link=f"/apps/{app.id}",
        data={
            "app_id": str(app.id),
            "app_name": app.name,
            "subject": subject,
            "version": version,
            "status": status,
        },
    )
    ar, en = _SUBJECT_LABELS[subject]
    url = settings.merchant_hub_url.replace("://merchant.", "://partners.", 1)
    try:
        await ResendEmailService().send_email(
            EmailMessage(
                to=await _recipients(db, partner),
                subject=partner_app_status_subject(status, app.name),
                html_content=partner_app_status_html(
                    status=status,
                    app_name=app.name,
                    subject_label=(ar.format(v=version), en.format(v=version)),
                    notes=notes,
                    url=f"{url.rstrip('/')}/apps/{app.id}",
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("partner_status_email_failed", status=status, error=str(exc))
